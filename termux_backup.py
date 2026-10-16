"""Encrypted full-state backup/restore for the Termux NSE research project.

The backup contains the private NSE data directory and the ignored .env file.
It does not contain Git-tracked source code; source is recovered from GitHub.
The backend should be stopped while creating/restoring a backup so SQLite files
are consistent.
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"NSEBAK1\n"
CHUNK = 1024 * 1024
SALT_LEN = 16
NONCE_PREFIX_LEN = 8

EXCLUDED_NAMES = {
    "termux_supervisor.pid",
    "termux_supervisor.lock",
    "worker_lock.sqlite-journal",
    "research_worker.lock",
}


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise ValueError("Backup passphrase must be at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _skip(path: Path) -> bool:
    name = path.name
    return (
        name in EXCLUDED_NAMES
        or name.endswith(".pid")
        or name.endswith(".lock")
        or name.endswith(".tmp")
    )


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        if destination != target and destination not in target.parents:
            raise ValueError("Unsafe path in backup archive")
        if member.issym() or member.islnk():
            raise ValueError("Links are not allowed in backup archive")
    tar.extractall(destination)


def _build_tar(code_root: Path, data_root: Path, tar_path: Path) -> None:
    code_root = code_root.resolve()
    data_root = data_root.resolve()
    if not data_root.is_dir():
        raise FileNotFoundError(f"Private data directory not found: {data_root}")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": "NSE_TERMUX_FULL_STATE_V1",
        "data_directory_name": data_root.name,
        "includes_env": (code_root / ".env").is_file(),
        "note": "Git source code is restored separately from origin/main.",
    }

    with tarfile.open(tar_path, "w:gz") as tar:
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(payload))

        env_path = code_root / ".env"
        if env_path.is_file():
            tar.add(env_path, arcname=".env", recursive=False)

        for path in sorted(data_root.rglob("*")):
            rel = path.relative_to(data_root)
            if _skip(path):
                continue
            if any(_skip(parent) for parent in rel.parents if str(parent) != "."):
                continue
            arcname = Path("data") / rel
            tar.add(path, arcname=str(arcname), recursive=False)


def _encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    salt = os.urandom(SALT_LEN)
    prefix = os.urandom(NONCE_PREFIX_LEN)
    key = _derive_key(passphrase, salt)
    aes = AESGCM(key)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as inp, destination.open("wb") as out:
        out.write(MAGIC)
        out.write(salt)
        out.write(prefix)
        counter = 0
        while True:
            plain = inp.read(CHUNK)
            if not plain:
                break
            nonce = prefix + struct.pack(">I", counter)
            aad = MAGIC + struct.pack(">I", counter)
            cipher = aes.encrypt(nonce, plain, aad)
            out.write(struct.pack(">I", len(cipher)))
            out.write(cipher)
            counter += 1
        out.write(struct.pack(">I", 0))
    try:
        destination.chmod(0o600)
    except OSError:
        pass


def _decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    with source.open("rb") as inp:
        if inp.read(len(MAGIC)) != MAGIC:
            raise ValueError("Not an NSE encrypted backup")
        salt = inp.read(SALT_LEN)
        prefix = inp.read(NONCE_PREFIX_LEN)
        if len(salt) != SALT_LEN or len(prefix) != NONCE_PREFIX_LEN:
            raise ValueError("Truncated backup header")
        key = _derive_key(passphrase, salt)
        aes = AESGCM(key)
        with destination.open("wb") as out:
            counter = 0
            while True:
                raw_len = inp.read(4)
                if len(raw_len) != 4:
                    raise ValueError("Truncated encrypted backup")
                length = struct.unpack(">I", raw_len)[0]
                if length == 0:
                    if inp.read(1):
                        raise ValueError("Unexpected trailing backup data")
                    break
                cipher = inp.read(length)
                if len(cipher) != length:
                    raise ValueError("Truncated encrypted chunk")
                nonce = prefix + struct.pack(">I", counter)
                aad = MAGIC + struct.pack(">I", counter)
                out.write(aes.decrypt(nonce, cipher, aad))
                counter += 1


def create_backup(code_root: Path, data_root: Path, destination: Path, passphrase: str) -> Path:
    code_root = Path(code_root).resolve()
    data_root = Path(data_root).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {destination}")
    with tempfile.TemporaryDirectory(prefix="nse-backup-") as tmp:
        tar_path = Path(tmp) / "payload.tar.gz"
        _build_tar(code_root, data_root, tar_path)
        _encrypt_file(tar_path, destination, passphrase)
    return destination


def restore_backup(code_root: Path, data_root: Path, source: Path, passphrase: str) -> dict:
    code_root = Path(code_root).resolve()
    data_root = Path(data_root).expanduser().resolve()
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Backup not found: {source}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_data = None
    rollback_env = None

    with tempfile.TemporaryDirectory(prefix="nse-restore-") as tmp:
        tmp_root = Path(tmp)
        tar_path = tmp_root / "payload.tar.gz"
        stage = tmp_root / "stage"
        stage.mkdir()
        _decrypt_file(source, tar_path, passphrase)
        with tarfile.open(tar_path, "r:gz") as tar:
            _safe_extract(tar, stage)

        manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "NSE_TERMUX_FULL_STATE_V1":
            raise ValueError("Unsupported backup format")
        staged_data = stage / "data"
        if not staged_data.is_dir():
            raise ValueError("Backup has no private data payload")

        if data_root.exists():
            rollback_data = data_root.with_name(data_root.name + f".pre-restore-{stamp}")
            if rollback_data.exists():
                raise FileExistsError(f"Rollback path already exists: {rollback_data}")
            data_root.rename(rollback_data)
        shutil.copytree(staged_data, data_root)
        data_root.chmod(0o700)
        (data_root / "runtime").mkdir(parents=True, exist_ok=True, mode=0o700)
        for pattern in ("*.pid", "*.lock"):
            for stale in (data_root / "runtime").glob(pattern):
                stale.unlink(missing_ok=True)

        staged_env = stage / ".env"
        env_path = code_root / ".env"
        if staged_env.is_file():
            if env_path.exists():
                rollback_env = code_root / f".env.pre-restore-{stamp}"
                env_path.rename(rollback_env)
            shutil.copy2(staged_env, env_path)
            env_path.chmod(0o600)

    return {
        "restored_data": str(data_root),
        "rollback_data": str(rollback_data) if rollback_data else "",
        "restored_env": (code_root / ".env").is_file(),
        "rollback_env": str(rollback_env) if rollback_env else "",
    }


def _default_destination() -> Path:
    shared = Path.home() / "storage" / "shared" / "Download" / "NSE_Project_Backup"
    if not shared.parent.exists():
        raise FileNotFoundError(
            "Shared storage is not available. Run termux-setup-storage first and grant permission."
        )
    shared.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return shared / f"nse_project_{stamp}.nsebak"


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--code-root", type=Path, required=True)
    backup.add_argument("--data-root", type=Path, required=True)
    backup.add_argument("--output", type=Path)

    restore = sub.add_parser("restore")
    restore.add_argument("--code-root", type=Path, required=True)
    restore.add_argument("--data-root", type=Path, required=True)
    restore.add_argument("--input", type=Path, required=True)

    args = parser.parse_args()
    first = getpass.getpass("Backup passphrase (minimum 12 characters): ")
    if args.command == "backup":
        second = getpass.getpass("Confirm backup passphrase: ")
        if first != second:
            raise SystemExit("Passphrases do not match")
        output = args.output or _default_destination()
        path = create_backup(args.code_root, args.data_root, output, first)
        print(f"Encrypted full-state backup created: {path}")
        print("Keep the passphrase separately. It is not stored in the backup.")
    else:
        result = restore_backup(args.code_root, args.data_root, args.input, first)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
