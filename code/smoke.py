import time, sys, numpy as np
from rateq.instances import *
from rateq.model import brute_vec, INF
from rateq.index import RQIndex, RQIndexPlus
import os
Cls = RQIndexPlus if os.environ.get('PLUS') else RQIndex
N=int(sys.argv[1]) if len(sys.argv)>1 else 5000
for name, mk in [("PUB", build_pub), ("PUBx120", lambda: build_pub_scaled(120)), ("SYN200", lambda: build_syn(200)), ("ERPSTYLE20", lambda: build_erp_style(20))]:
    inst=mk()
    for s in inst.services: assert (np.diff(s.zstart)>0).all(), s.name
    for wl, P in [("olist", parcels_olist(N)), ("furn", parcels_furniture(N))]:
        t=time.time(); b,a = brute_vec(inst,P); tb=time.time()-t
        t=time.time(); ix=Cls(inst); tbuild=time.time()-t
        ix.query_batch(P); st=[]; t=time.time(); b2,a2 = ix.query_batch(P, st); tq=time.time()-t  # warm
        ok=bool((b==b2).all() and (a==a2).all())
        bad=0
        for i in range(200):
            r=ix.query1(int(P.key[i]),float(P.a[i]),float(P.L[i]),float(P.M[i]),float(P.S[i]),float(P.res[i]),int(P.day[i]))
            if r!=(int(b[i]),int(a[i])): bad+=1
        print(name, wl, "sig",ix.n_sig,"groups",len(ix.groups),"brute",round(tb,3),"build",round(tbuild,2),"idx",round(tq,3),"exact",ok,"scalar_bad",bad,"evals/parcel",round(st[0]/len(P),2),"inf",(b>=INF).mean().round(3), "MB", round(ix.nbytes()/1e6,1), flush=True)
