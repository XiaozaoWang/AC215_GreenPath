"""Standalone labeling stage extracted from en_04_labeling.ipynb; no notebook runtime required."""

if __name__ == "__main__":
    from greenpath_source.runtime import context
    globals().update(context("labeling", SOURCE_OPTIONS))
    # Extracted from en_04_labeling.ipynb, cell 7.
    import numpy as np, hashlib, random, threading, time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    from tqdm.auto import tqdm

    PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
    LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if MAX_IMAGES > 0 and not PROJECT:
        raise ValueError("Set GOOGLE_CLOUD_PROJECT before paid labeling")

    DIMS = ["traffic_intensity", "quietness", "shade", "naturalness",
            "scenic_appeal", "historic_character", "liveliness", "visual_openness"]

    sv = read_parquet(path("processed", "streetview.parquet"))
    if "downloaded" in sv:                                  # photos that failed to download in 01 have no file; skip them
        sv = sv[sv.downloaded.astype(bool)].reset_index(drop=True)
    cells = read_parquet(path("processed", "cells.parquet"))
    if sv.empty:
        raise ValueError("No downloaded street images to label")
    print(f"Project {PROJECT} · region {LOCATION} · model {MODEL}")
    print(f"To process: {len(sv)} street photos, {sv.cell_id.nunique()} cells")

    # Extracted from en_04_labeling.ipynb, cell 9.
    import json
    from pydantic import BaseModel, Field
    from google import genai
    from google.genai import types, errors as genai_errors

    class Scores(BaseModel):
        traffic_intensity: int = Field(ge=0, le=5, strict=True)
        quietness: int = Field(ge=0, le=5, strict=True)
        shade: int = Field(ge=0, le=5, strict=True)
        naturalness: int = Field(ge=0, le=5, strict=True)
        scenic_appeal: int = Field(ge=0, le=5, strict=True)
        historic_character: int = Field(ge=0, le=5, strict=True)
        liveliness: int = Field(ge=0, le=5, strict=True)
        visual_openness: int = Field(ge=0, le=5, strict=True)

    class ImageLabel(BaseModel):
        usable: bool
        unusable_reason: str
        scores: Scores
        rationale: str

    PROMPT = open(os.path.join(PROMPT_DIR, f"{PROMPT_VERSION}.txt"), encoding="utf-8").read()
    PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()

    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION) if MAX_IMAGES > 0 else None
    request_spec = {"model": MODEL, "prompt_sha256": PROMPT_SHA, "thinking_budget": THINKING_BUDGET,
                    "temperature": 0.0, "location": LOCATION, "schema": "greenpath-image-label-v1"}
    REQUEST_SPEC_SHA = hashlib.sha256(json.dumps(request_spec, sort_keys=True).encode()).hexdigest()
    sv["image_sha256"] = [file_digest(path(name)) for name in sv.image_path]
    sv["cache_key"] = [hashlib.sha256(f"{image_id}:{sha}:{REQUEST_SPEC_SHA}".encode()).hexdigest()
                       for image_id, sha in zip(sv.image_id, sv.image_sha256)]
    if sv.image_id.duplicated().any():
        raise ValueError("Street-image IDs must be unique")
    CURRENT_KEYS = set(sv.cache_key)
    JOURNAL = path("processed", "label-cache")
    makedirs(JOURNAL)

    def current_mask(frame):
        return frame.cache_key.isin(CURRENT_KEYS)


    def call_model(img_bytes):
        # returns (the JSON text of the reply, number of input tokens, number of output tokens)
        cfg = dict(temperature=0.0, response_mime_type="application/json",
                   response_schema=ImageLabel, max_output_tokens=2048)
        if THINKING_BUDGET is not None:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=THINKING_BUDGET)
        resp = client.models.generate_content(
            model=MODEL,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"), PROMPT],
            config=types.GenerateContentConfig(**cfg))
        u = resp.usage_metadata
        tin = (u.prompt_token_count or 0) if u else 0
        tout = ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
        return resp.text, tin, tout

    FATAL_HINTS = {
        400: "A parameter is not supported: try setting THINKING_BUDGET to None first",
        401: "Not logged in: run gcloud auth application-default login",
        403: "The project has not enabled the Vertex AI API, has no billing linked, or the account lacks permission",
        404: "This model is not available in this region: change MODEL or GOOGLE_CLOUD_LOCATION",
    }

    class FatalAPIError(RuntimeError):
        pass

    def check_label(label):
        sc = label.scores.model_dump()
        if label.usable:
            bad = {k: v for k, v in sc.items() if not 1 <= v <= 5}
            if bad:
                raise ValueError(f"Scores of a usable photo must be between 1 and 5: {bad}")
        else:
            sc = {k: 0 for k in sc}
        return sc

    def score_image(image_id, rel_path, tries=5):
        with fs.open(path(rel_path), "rb") as f:
            data = f.read()
        last = None
        for k in range(tries):
            if abort.is_set():
                return None
            try:
                text, tin, tout = call_model(data)
                label = ImageLabel.model_validate_json(text)
                sc = check_label(label)
                return {"image_id": image_id, "prompt_version": PROMPT_VERSION, "prompt_sha": PROMPT_SHA,
                        "model": MODEL, "usable": bool(label.usable),
                        "unusable_reason": label.unusable_reason, **sc,
                        "rationale": label.rationale, "input_tokens": tin, "output_tokens": tout,
                        "scored_at": now_iso()}
            except genai_errors.APIError as e:
                if e.code in FATAL_HINTS:
                    raise FatalAPIError(f"Vertex AI returned {e.code}: {FATAL_HINTS[e.code]}") from None
                last = e
                time.sleep(min(60, 2 ** k + random.random()))      # 429 / 5xx: back off and retry
            except ValueError as e:                                  # the returned JSON is not in the expected format
                last = e
                time.sleep(1)
        raise RuntimeError(f"Label request failed after {tries} attempts: {type(last).__name__}") from None

    def cost_usd(tin, tout):
        if PRICE_IN_PER_M is None or PRICE_OUT_PER_M is None:
            return None
        return tin / 1e6 * PRICE_IN_PER_M + tout / 1e6 * PRICE_OUT_PER_M

    # Extracted from en_04_labeling.ipynb, cell 13.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    LABELS_REL = "processed/labels_image.parquet"
    LABEL_COLS = ["image_id", "prompt_version", "prompt_sha", "model", "usable", "unusable_reason", *DIMS,
                  "rationale", "input_tokens", "output_tokens", "scored_at", "image_sha256", "cache_key", "request_spec_sha256"]
    journal_rows = []
    for name in sorted(os.listdir(JOURNAL)):
        if name.endswith(".json"):
            with open(os.path.join(JOURNAL, name)) as stream:
                saved = json.load(stream)
            if saved.get("cache_key") != name[:-5]:
                raise ValueError("Label journal key does not match its filename")
            journal_rows.append(saved)
    cache = pd.DataFrame(journal_rows, columns=LABEL_COLS)
    done = set(cache.cache_key)
    todo = sv[~sv.cache_key.isin(done)].head(MAX_IMAGES)
    print(f"{len(CURRENT_KEYS & done)} current photos cached; at most {len(todo)} new photos selected")
    errors, abort = {}, threading.Event()

    def work(r):
        if abort.is_set():
            return None
        row = score_image(r.image_id, r.image_path)
        if row is None:
            return None
        row.update({"image_sha256": r.image_sha256, "cache_key": r.cache_key,
                    "request_spec_sha256": REQUEST_SPEC_SHA})
        # Persist each paid result before returning; the parquet can be rebuilt from this journal.
        safe_write_json(os.path.join(JOURNAL, r.cache_key + ".json"), row)
        return row

    fatal, new_rows = None, []
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(work, r): r for r in todo.itertuples()}
            for fut in tqdm(as_completed(futs), total=len(futs), desc="scoring"):
                r = futs[fut]
                try:
                    row = fut.result()
                    if row:
                        new_rows.append(row)
                except FatalAPIError as exc:
                    fatal = exc
                    abort.set()
                    for pending in futs:
                        pending.cancel()
                except Exception as exc:
                    if not fut.cancelled():
                        errors[r.image_id] = type(exc).__name__
    finally:
        # Successful worker results have already been atomically journaled, including on interruption.
        journal_rows = [json.load(open(os.path.join(JOURNAL, name)))
                        for name in sorted(os.listdir(JOURNAL)) if name.endswith(".json")]
        cache = pd.DataFrame(journal_rows, columns=LABEL_COLS)
        write_parquet(cache, path(LABELS_REL))
        safe_write_json(path("processed", "label_errors.json"), errors)
    if fatal:
        raise fatal
    mine = cache[current_mask(cache)]
    print(json.dumps({"current_cached_images": len(mine), "failed_this_run": len(errors),
                      "input_tokens": int(mine.input_tokens.sum()), "output_tokens": int(mine.output_tokens.sum()),
                      "estimated_cost_usd": cost_usd(int(mine.input_tokens.sum()), int(mine.output_tokens.sum()))}))
    if mine.empty:
        raise ValueError("No current labels are cached; run labeling with a positive request limit")

    # Extracted from en_04_labeling.ipynb, cell 15.
    mine = cache[current_mask(cache)].copy()
    mine["usable"] = mine.usable.astype(bool)
    for d in DIMS:
        mine[d] = pd.to_numeric(mine[d])
    lab = mine.merge(sv[["image_id", "cell_id", "h3_index"]], on="image_id", how="inner")
    ok = lab[lab.usable]

    g = ok.groupby(["cell_id", "h3_index"])
    agg = g[DIMS].mean().add_suffix("_mean").join(g[DIMS].std(ddof=0).add_suffix("_std")).join(g.size().rename("n_usable")).reset_index()
    labels_cell = cells[["cell_id", "h3_index"]].merge(agg, on=["cell_id", "h3_index"], how="left")
    labels_cell["n_images"] = labels_cell.cell_id.map(sv.groupby("cell_id").size()).fillna(0).astype(int)
    labels_cell["n_usable"] = labels_cell.n_usable.fillna(0).astype(int)
    enough = labels_cell.n_usable >= MIN_USABLE_IMAGES
    for d in DIMS:
        labels_cell[d] = ((labels_cell[f"{d}_mean"] - 1) / 4).where(enough)          # normalize to 0–1
        labels_cell[f"{d}_std"] = (labels_cell[f"{d}_std"] / 4).where(enough)
    labels_cell = labels_cell[["cell_id", "h3_index", "n_images", "n_usable", *DIMS, *[f"{d}_std" for d in DIMS]]]
    labels_cell["prompt_version"] = PROMPT_VERSION
    labels_cell["model"] = MODEL
    write_parquet(labels_cell, path("processed", "labels_cell.parquet"))

    meta = {"model": MODEL, "prompt_version": PROMPT_VERSION, "prompt_sha": PROMPT_SHA, "location": LOCATION,
            "thinking_budget": THINKING_BUDGET, "temperature": 0.0, "min_usable_images": MIN_USABLE_IMAGES,
            "images_scored": int(len(mine)), "images_usable": int(mine.usable.sum()),
            "input_tokens": int(mine.input_tokens.sum()), "output_tokens": int(mine.output_tokens.sum()),
            "est_cost_usd": cost_usd(int(mine.input_tokens.sum()), int(mine.output_tokens.sum())),
            "request_spec_sha256": REQUEST_SPEC_SHA, "pricing_note": "User-supplied token rates only; null means no pricing estimate configured",
            "images_pending": int(len(sv) - len(mine)), "complete": bool(len(mine) == len(sv)),
            "cells_total": int(len(labels_cell)), "cells_labeled": int(enough.sum()), "created_at": now_iso()}
    with fs.open(path("processed", "labels_meta.json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"labels_cell.parquet: {len(labels_cell)} cells, of which {int(enough.sum())} have labels (usable photos ≥ {MIN_USABLE_IMAGES})")

    # Extracted from en_04_labeling.ipynb, cell 17.
    import base64, io as _io
    from concurrent.futures import ThreadPoolExecutor

    GALLERY_REL = "processed/labels_gallery.html"
    SHORT = {"traffic_intensity": "Traffic", "quietness": "Quiet", "shade": "Shade", "naturalness": "Nature",
             "scenic_appeal": "Scenic", "historic_character": "Historic", "liveliness": "Lively", "visual_openness": "Open"}

    def make_thumb(rel_path):
        with fs.open(path(rel_path), "rb") as f:
            im = Image.open(f).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        buf = _io.BytesIO(); im.save(buf, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    allv = cache.copy()
    allv["usable"] = allv.usable.astype(bool)
    is_cur = current_mask(allv)
    cur = allv[is_cur].merge(sv[["image_id", "cell_id", "image_path", "captured_at", "compass"]], on="image_id", how="inner")
    if GALLERY_MAX_IMAGES and len(cur) > GALLERY_MAX_IMAGES:
        cur = cur.sample(n=GALLERY_MAX_IMAGES, random_state=SEED)
    cur = cur.sort_values(["cell_id", "image_id"]).reset_index(drop=True)

    others = {}
    for r in allv[~is_cur].itertuples():
        tag = r.prompt_version + ("" if r.model == MODEL else f" · {r.model}")
        others.setdefault(r.image_id, []).append({"n": tag, "u": bool(r.usable), "s": [int(getattr(r, d)) for d in DIMS],
                                                  "r": r.rationale})

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        thumbs = list(tqdm(ex.map(make_thumb, cur.image_path), total=len(cur), desc="Generating thumbnails"))

    records = []
    for r, th in zip(cur.itertuples(), thumbs):
        records.append({"id": r.image_id, "c": r.cell_id[-8:], "cf": r.cell_id, "t": th, "h": "../" + r.image_path,
                        "u": bool(r.usable), "x": r.unusable_reason, "s": {d: int(getattr(r, d)) for d in DIMS},
                        "r": r.rationale, "d": str(r.captured_at)[:10], "a": round(float(r.compass)),
                        "v": others.get(r.image_id, [])})

    GALLERY_HTML = r"""<!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>GreenPath Label Gallery</title>
    <style>
    :root{--bg:#f6f6f3;--card:#fff;--ink:#1c1c1a;--muted:#6a6a64;--line:#e1e1da;--accent:#2f7d57;--bad:#b3392f;--track:#ececE6}
    @media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#222220;--ink:#ecece7;--muted:#9a9a92;--line:#363631;--accent:#5bbb89;--bad:#e8786d;--track:#33332f}}
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
    header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
    h1{font-size:16px;margin:0 0 8px;font-weight:600}
    .ctl{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
    .ctl label{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:13px}
    select,input{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
    input[type=number]{width:56px}input[type=text]{width:120px}
    button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 12px;cursor:pointer}
    button:disabled{opacity:.4;cursor:default}
    #info{color:var(--muted);font-size:13px;margin-top:8px}
    main{padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
    .grp{grid-column:1/-1;font-weight:600;padding:6px 0 0;border-bottom:1px solid var(--line)}
    .grp span{color:var(--muted);font-weight:400;margin-left:8px;font-size:13px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
    .card.bad{border-color:var(--bad)}
    .card img{width:100%;height:210px;object-fit:cover;display:block;background:var(--track)}
    .body{padding:10px 12px 12px}
    .meta{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-bottom:6px}
    .row{display:grid;grid-template-columns:34px 1fr 16px;gap:8px;align-items:center;margin:3px 0;font-size:13px}
    .bar{height:8px;background:var(--track);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--accent)}
    .row b{text-align:right}
    .why{margin-top:8px;color:var(--muted);font-size:12.5px}
    .warn{color:var(--bad);font-weight:600}
    details{margin-top:8px;font-size:12px;color:var(--muted)}
    details table{border-collapse:collapse;margin-top:4px}details td{padding:1px 6px 1px 0}
    .diff{color:var(--bad);font-weight:600}
    @media (max-width:520px){main{padding:12px;grid-template-columns:1fr}}
    </style></head><body>
    <header>
    <h1>GreenPath Label Gallery <small style="color:var(--muted);font-weight:400">__META__</small></h1>
    <div class="ctl">
    <label>Status<select id="fu"><option value="all">All</option><option value="1">Usable only</option><option value="0">Unusable only</option></select></label>
    <label>Cell<input type="text" id="fc" placeholder="cell_id fragment"></label>
    <label>Dimension<select id="fd"><option value="">No filter</option></select>
     <input type="number" id="lo" min="1" max="5" value="1">–<input type="number" id="hi" min="1" max="5" value="5"></label>
    <label>Sort<select id="so"></select></label>
    <label><input type="checkbox" id="gp"> Group by cell</label>
    <label>Per page<select id="ps"><option>24</option><option selected>48</option><option>96</option></select></label>
    <span><button id="pv">Prev</button> <span id="pg"></span> <button id="nx">Next</button></span>
    </div><div id="info"></div></header>
    <main id="main"></main>
    <script>
    const DATA=__DATA__;const DIMS=__DIMS__;const LABEL=__LABEL__;
    const $=id=>document.getElementById(id);
    const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    let page=0,rows=[];
    DIMS.forEach(k=>{$("fd").add(new Option(LABEL[k],k));
     $("so").add(new Option(LABEL[k]+" high→low","h:"+k));$("so").add(new Option(LABEL[k]+" low→high","l:"+k));});
    $("so").insertBefore(new Option("Cell (default)","cell"),$("so").firstChild);$("so").value="cell";
    function apply(){
     const u=$("fu").value,c=$("fc").value.trim().toLowerCase(),d=$("fd").value,lo=+$("lo").value,hi=+$("hi").value;
     rows=DATA.filter(x=>(u==="all"||(u==="1")===x.u)&&(!c||x.cf.toLowerCase().includes(c))&&(!d||(x.u&&x.s[d]>=lo&&x.s[d]<=hi)));
     const so=$("so").value;
     if(so.includes(":")){const [m,k]=so.split(":");rows.sort((a,b)=>(m==="h"?-1:1)*((a.u?a.s[k]:99*(m==="h"?-1:1))-(b.u?b.s[k]:99*(m==="h"?-1:1))));}
     else rows.sort((a,b)=>a.cf<b.cf?-1:a.cf>b.cf?1:(a.id<b.id?-1:1));
     if($("gp").checked&&so.includes(":"))rows.sort((a,b)=>a.cf<b.cf?-1:a.cf>b.cf?1:0);
     page=0;render();}
    function card(x){
     const bars=DIMS.map(k=>`<div class="row"><span>${LABEL[k]}</span><span class="bar"><i style="width:${x.u?x.s[k]*20:0}%"></i></span><b>${x.u?x.s[k]:"–"}</b></div>`).join("");
     const why=x.u?esc(x.r):`<span class="warn">Unusable: </span>${esc(x.x)}`;
     let ver="";
     if(x.v.length){ver=`<details><summary>Other versions (${x.v.length})</summary><table><tr><td></td>${DIMS.map(k=>`<td>${LABEL[k]}</td>`).join("")}</tr>`+
      [{n:"current",u:x.u,s:DIMS.map(k=>x.s[k])}].concat(x.v).map(v=>`<tr><td>${esc(v.n)}</td>${v.s.map((s,i)=>{const cur=x.s[DIMS[i]];const df=v.u&&x.u&&Math.abs(s-cur)>=2;return `<td class="${df?"diff":""}">${v.u?s:"–"}</td>`}).join("")}</tr>`).join("")+`</table></details>`;}
     return `<div class="card ${x.u?"":"bad"}"><a href="${esc(x.h)}" target="_blank" title="Click to open the original image"><img src="${x.t}" loading="lazy" alt=""></a>
     <div class="body"><div class="meta"><span>${esc(x.c)} · ${esc(x.id)}</span><span>${esc(x.d)} · ${x.a}°</span></div>${bars}<div class="why">${why}</div>${ver}</div></div>`;}
    function render(){
     const ps=+$("ps").value,np=Math.max(1,Math.ceil(rows.length/ps));page=Math.min(page,np-1);
     const cells={};rows.forEach(x=>{(cells[x.cf]=cells[x.cf]||[]).push(x)});
     const grouped=$("gp").checked;let html="",last=null;
     rows.slice(page*ps,(page+1)*ps).forEach(x=>{
      if(grouped&&x.cf!==last){const g=cells[x.cf];html+=`<div class="grp">${esc(x.cf)}<span>${g.length} images · ${g.filter(y=>y.u).length} usable</span></div>`;last=x.cf;}
      html+=card(x);});
     $("main").innerHTML=html||'<p style="grid-column:1/-1;color:var(--muted)">No images match the filters</p>';
     $("pg").textContent=`Page ${page+1} / ${np}`;$("pv").disabled=page===0;$("nx").disabled=page>=np-1;
     const nu=rows.filter(x=>x.u).length;
     $("info").textContent=`${rows.length} images match (${nu} usable, ${rows.length-nu} unusable) of ${DATA.length} total, ${new Set(rows.map(x=>x.cf)).size} cells`;
     window.scrollTo(0,0);}
    ["fu","fc","fd","lo","hi","so","gp","ps"].forEach(i=>$(i).addEventListener("input",apply));
    $("pv").onclick=()=>{page--;render()};$("nx").onclick=()=>{page++;render()};
    apply();
    </script></body></html>"""

    gallery_meta = f"{PROMPT_VERSION} · {MODEL} · {THUMB_PX}px thumbnails, click to open the original"
    html = (GALLERY_HTML.replace("__META__", __import__("html").escape(gallery_meta))
            .replace("__DIMS__", json.dumps(DIMS)).replace("__LABEL__", json.dumps(SHORT, ensure_ascii=False))
            .replace("__DATA__", json.dumps(records, ensure_ascii=True).replace("</", "<\\/")))
    with fs.open(path(GALLERY_REL), "wb") as f:
        f.write(html.encode("utf-8"))
    size_mb = len(html.encode()) / 1e6
    print(f"Gallery generated: {GALLERY_REL} ({len(records)} photos, {size_mb:.1f} MB)")
    print("Open in a browser:", os.path.abspath(path(GALLERY_REL)) if STORAGE != "gcs" else path(GALLERY_REL))

    # Extracted from en_04_labeling.ipynb, cell 19.
    fig, axes = plt.subplots(2, 4, figsize=(14, 5.5))
    rows = []
    for ax, d in zip(axes.ravel(), DIMS):
        vc = ok[d].value_counts(normalize=True).reindex([1, 2, 3, 4, 5]).fillna(0)
        ax.bar(vc.index, vc.values); ax.set_title(d, fontsize=9); ax.set_ylim(0, 1); ax.set_xticks([1, 2, 3, 4, 5])
        rows.append({"Dimension": d, "Mean": ok[d].mean(), "Std": ok[d].std(), "Top-level share": vc.max(),
                     "Within-cell std": agg[f"{d}_std"].mean() if len(agg) else np.nan})
    plt.tight_layout(); plt.savefig(path("processed", "label_distributions.png")); plt.close(fig)
    qc = pd.DataFrame(rows).set_index("Dimension").round(2)
    qc["Warning"] = np.where((qc["Top-level share"] > 0.6) | (qc["Std"] < 0.6), "⚠ low discrimination", "")
    write_parquet(qc.reset_index(), path("processed", "labels_quality.parquet"))

    # Extracted from en_04_labeling.ipynb, cell 21.
    from scipy.stats import spearmanr

    op = path("processed", "osm_features.parquet")
    if fs.exists(op):
        osm = read_parquet(op)
        m = labels_cell.merge(osm, on=["cell_id", "h3_index"], how="inner")
        checks = [("traffic_intensity", "road_major_m", +1), ("quietness", "road_major_m", -1),
                  ("shade", "wc_tree_frac", +1), ("naturalness", "osm_green_frac", +1),
                  ("naturalness", "wc_built_frac", -1), ("visual_openness", "osm_building_frac", -1),
                  ("liveliness", "poi_count", +1), ("scenic_appeal", "osm_green_frac", +1)]
        out = []
        for d, f, sign in checks:
            x = m[[d, f]].dropna()
            if len(x) >= 8 and x[d].nunique() > 1 and x[f].nunique() > 1:
                r = spearmanr(x[d], x[f]).correlation
                out.append({"Dimension": d, "Reference feature": f, "Expected sign": "+" if sign > 0 else "−", "Samples": len(x),
                            "Spearman": round(r, 2), "Result": "✓" if r * sign > 0.2 else "✗ check"})
        write_parquet(pd.DataFrame(out, columns=["Dimension", "Reference feature", "Expected sign", "Samples", "Spearman", "Result"]), path("processed", "labels_correlations.parquet"))
    else:
        print("osm_features.parquet does not exist yet; run 03 first")

    # Extracted from en_04_labeling.ipynb, cell 23.
    qa_dir = "processed/qa/human_images"
    makedirs(path("processed", "qa")); makedirs(path(qa_dir))
    pick = ok.drop_duplicates("image_id").merge(sv[["image_id", "image_path"]], on="image_id")
    pick = pick.sample(n=min(QA_N, len(pick)), random_state=SEED)

    for r in tqdm(list(pick.itertuples()), desc="Copying photos"):
        dst = path(qa_dir, f"{r.image_id}.jpg")
        if not fs.exists(dst):
            with fs.open(path(r.image_path), "rb") as src, fs.open(dst, "wb") as out:
                out.write(src.read())

    sheet = pd.DataFrame({"image_id": pick.image_id.values, "file": [f"{qa_dir}/{i}.jpg" for i in pick.image_id],
                          **{d: "" for d in DIMS}, "rater": "", "notes": ""})
    sheet_path = path("processed", "qa", "human_label_sheet.csv")
    if not fs.exists(sheet_path):
        with fs.open(sheet_path, "w") as f:
            sheet.to_csv(f, index=False)
    else:
        print("Existing human label sheet preserved; it may reflect an earlier subset")
    print(f"Exported {len(sheet)} photos and a blank scoring sheet: processed/qa/human_label_sheet.csv")
    print("Each rater who scores should copy the sheet and fill in their own name under rater.")

    # Extracted from en_04_labeling.ipynb, cell 25.
    manifest = write_manifest({
        "step": "04_labeling", "model": MODEL, "location": LOCATION,
        "prompt_version": PROMPT_VERSION, "prompt_sha": PROMPT_SHA,
        "images_total": int(len(sv)), "images_scored_cached": int(len(mine)),
        "images_failed_this_run": int(len(errors)), "images_usable": int(mine.usable.sum()),
        "cells_total": int(len(labels_cell)), "cells_labeled": int(enough.sum()),
        "input_tokens": meta["input_tokens"], "output_tokens": meta["output_tokens"], "est_cost_usd": meta["est_cost_usd"],
        "outputs": ["processed/labels_image.parquet", "processed/labels_cell.parquet",
                    "processed/labels_meta.json", "processed/qa/human_label_sheet.csv"],
        "finished_at": now_iso(),
    }, "04_labeling_manifest.json")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    if errors:
        raise RuntimeError("Some labels failed; successful results are cached. Resume labeling to retry missing images.")
