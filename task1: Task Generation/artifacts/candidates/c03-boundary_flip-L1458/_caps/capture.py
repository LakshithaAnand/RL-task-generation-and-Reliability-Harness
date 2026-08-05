import json, tabulate
T = tabulate.tabulate
specs = json.load(open("/caps/specs.json"))
out = {}
for s in specs:
    try:
        out[s["id"]] = {"ok": True, "val": eval(s["code"], {"tabulate": tabulate, "T": T})}
    except Exception as e:  # noqa: BLE001
        out[s["id"]] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:200]}"}
print(json.dumps(out))
