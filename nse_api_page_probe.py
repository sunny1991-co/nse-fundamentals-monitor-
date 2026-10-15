import time, requests
LANDING="https://www.nseindia.com/companies-listing/corporate-integrated-filing"
API="https://www.nseindia.com/api/integrated-filing-results"
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":LANDING,"X-Requested-With":"XMLHttpRequest"})
r=s.get(LANDING,timeout=30); print("LANDING",r.status_code); r.raise_for_status()
for size in [100,500,1000]:
    t=time.time()
    try:
        r=s.get(API,params={"type":"Integrated Filing- Financials","index":"equities","page":0,"size":size},timeout=45)
        print("SIZE",size,"HTTP",r.status_code,"SECONDS",round(time.time()-t,2),"BYTES",len(r.content))
        r.raise_for_status(); j=r.json()
        print("KEYS",list(j)[:10] if isinstance(j,dict) else type(j).__name__)
        rec=None
        if isinstance(j,dict):
            for k in ["data","results","records","result","items"]:
                if isinstance(j.get(k),list): rec=j[k]; break
        elif isinstance(j,list): rec=j
        print("COUNT",len(rec or []),"TOTAL",j.get("totalCount") if isinstance(j,dict) else None)
    except Exception as e:
        print("SIZE_ERROR",size,type(e).__name__,str(e)[:300])
