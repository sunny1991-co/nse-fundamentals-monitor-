import time, requests
UAS="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
QUOTE_PATH="/api/quote-equity?symbol=RELIANCE"
bootstraps=[
 ("nse_root","https://www.nseindia.com/","https://www.nseindia.com/"),
 ("nse_quote_page","https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE","https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE"),
 ("nse_market_page","https://www.nseindia.com/market-data/live-equity-market","https://www.nseindia.com/market-data/live-equity-market"),
 ("nse_in_root","https://www.nseindia.in/","https://www.nseindia.in/"),
]
for label, landing, referer in bootstraps:
    s=requests.Session()
    s.headers.update({
        "User-Agent":UAS,"Accept":"application/json, text/plain, */*",
        "Accept-Language":"en-US,en;q=0.9","Accept-Encoding":"gzip, deflate, br",
        "Referer":referer,"X-Requested-With":"XMLHttpRequest",
        "Connection":"keep-alive",
    })
    try:
        a=s.get(landing,timeout=15,allow_redirects=True)
        print("LANDING",label,"HTTP",a.status_code,"FINAL",a.url,"BYTES",len(a.content))
        api_base="https://www.nseindia.in" if "nseindia.in" in landing else "https://www.nseindia.com"
        t=time.time()
        r=s.get(api_base+QUOTE_PATH,headers={"Referer":referer},timeout=15)
        print("QUOTE",label,"HTTP",r.status_code,"SECONDS",round(time.time()-t,2),"BYTES",len(r.content),"BODY",r.text[:180].replace("\n"," "))
        if r.ok:
            try: print("INDUSTRY",label,r.json().get("industryInfo"))
            except Exception as e: print("JSONERR",label,type(e).__name__,str(e)[:100])
    except Exception as e:
        print("ERROR",label,type(e).__name__,str(e)[:250])
