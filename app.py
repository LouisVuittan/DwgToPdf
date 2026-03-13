#!/usr/bin/env python3
"""
정수산업개발 — DWG/DXF → PDF 변환 서비스
- DXF: ezdxf + matplotlib
- DWG: ODA File Converter → DXF → PDF
- 한글 SHX → 시스템 한글 폰트 매핑
"""

import os, sys, uuid, time, shutil, zipfile, platform, subprocess, threading
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import matplotlib as mpl

import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, ColorPolicy, BackgroundPolicy
from ezdxf.fonts import fonts as ezdxf_fonts

from flask import Flask, request, send_file, jsonify, render_template_string

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "_uploads"
OUTPUT_DIR = BASE_DIR / "_outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  한글 폰트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def setup_korean_fonts():
    is_win = platform.system() == "Windows"
    keywords = [
        "Malgun Gothic","맑은 고딕","Gulim","Batang","Dotum",
        "NanumGothic","Nanum Gothic","NanumMyeongjo",
        "Noto Sans CJK KR","Noto Sans KR",
        "AppleGothic","Apple SD Gothic Neo",
    ]
    fp = fn = None
    for fi in fm.fontManager.ttflist:
        for kw in keywords:
            if kw.lower() in fi.name.lower():
                fp, fn = fi.fname, fi.name; break
        if fp: break

    if not fp and is_win:
        wf = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name, disp in [("malgun.ttf","Malgun Gothic"),("gulim.ttc","Gulim"),("batang.ttc","Batang")]:
            p = wf / name
            if p.exists(): fp, fn = str(p), disp; fm.fontManager.addfont(str(p)); break

    if not fp:
        for p in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                   "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                   "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"]:
            if os.path.isfile(p): fm.fontManager.addfont(p); fp, fn = p, "Noto Sans CJK KR"; break

    if fp:
        mpl.rcParams["font.sans-serif"] = [fn] + mpl.rcParams["font.sans-serif"]
        mpl.rcParams["axes.unicode_minus"] = False
        print(f"[폰트] {fn}")
    else:
        print("[폰트] ⚠ 한글 폰트 미발견")

    ttf = os.path.basename(fp) if fp else "malgun.ttf"
    m = {}
    for n in ["WHGTXT","WHGDTXT","WHGGTXT","WHGRTXT","KORGOT","KORGOTB","KORGT","KORGTB","WHTGTXT","WHTMTXT"]:
        m[n] = ttf; m[n+".SHX"] = ttf
    ezdxf_fonts.SHX_FONTS.update(m)
    return fp, fn

KO_PATH, KO_NAME = setup_korean_fonts()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ODA File Converter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def find_oda():
    candidates = []
    if platform.system() == "Windows":
        for base in [os.environ.get("ProgramFiles",""), os.environ.get("ProgramFiles(x86)",""), os.environ.get("LOCALAPPDATA","")]:
            if not base: continue
            od = Path(base) / "ODA"
            if od.exists():
                for sub in sorted(od.iterdir(), reverse=True):
                    e = sub / "ODAFileConverter.exe"
                    if e.exists(): candidates.append(str(e))
    else:
        candidates += ["/usr/local/bin/ODAFileConverter", "/usr/bin/ODAFileConverter"]
    for c in candidates:
        if os.path.isfile(c): return c
    return shutil.which("ODAFileConverter")

ODA = find_oda()
print(f"[ODA] {'✓ '+ODA if ODA else '✗ DXF만'}")


def dwg_to_dxf(dwg, out_dir):
    if not ODA:
        raise RuntimeError("DWG 변환에는 ODA File Converter 필요\nhttps://www.opendesign.com/guestfiles/oda_file_converter")
    subprocess.run([ODA, str(Path(dwg).parent), out_dir, "ACAD2018", "DXF", "0", "1", Path(dwg).name],
                   capture_output=True, text=True, timeout=120)
    dxf = Path(out_dir) / (Path(dwg).stem + ".dxf")
    if not dxf.exists(): raise RuntimeError("DWG → DXF 변환 실패")
    return str(dxf)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  변환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PAPER = {"a4":(8.27,11.69),"a3":(11.69,16.54),"a2":(16.54,23.39),"a1":(23.39,33.11),"a0":(33.11,46.81)}

def dxf_to_pdf(dxf_path, pdf_path, paper="a3", bg="white", dpi=300):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    fw, fh = PAPER.get(paper, PAPER["a3"])
    bgc = "#000000" if bg == "black" else "#ffffff"
    fgc = "#ffffff" if bg == "black" else "#000000"
    cfg = Configuration.defaults().with_changes(
        color_policy=ColorPolicy.COLOR, background_policy=BackgroundPolicy.CUSTOM,
        custom_bg_color=bgc, custom_fg_color=fgc)
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(pdf_path) as pdf:
        fig = plt.figure(figsize=(fw, fh), dpi=dpi)
        fig.patch.set_facecolor(bgc)
        ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
        ax.set_facecolor(bgc)
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(msp)
        ax.set_aspect("equal"); ax.autoscale_view(); ax.axis("off")
        pdf.savefig(fig, facecolor=fig.get_facecolor())
        plt.close(fig)

def convert_one(inp, out, paper="a3", bg="white", dpi=300):
    ext = Path(inp).suffix.lower()
    if ext == ".dwg":
        tmp = str(UPLOAD_DIR / f"oda_{uuid.uuid4().hex[:8]}")
        os.makedirs(tmp, exist_ok=True)
        try: dxf_to_pdf(dwg_to_dxf(inp, tmp), out, paper, bg, dpi)
        finally: shutil.rmtree(tmp, ignore_errors=True)
    elif ext == ".dxf":
        dxf_to_pdf(inp, out, paper, bg, dpi)
    else:
        raise ValueError(f"지원하지 않는 형식: {ext}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  자동 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cleanup():
    while True:
        time.sleep(600); now = time.time()
        for d in [UPLOAD_DIR, OUTPUT_DIR]:
            if not d.exists(): continue
            for f in d.iterdir():
                if f.is_file() and now - f.stat().st_mtime > 1800: f.unlink(missing_ok=True)
threading.Thread(target=cleanup, daemon=True).start()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  라우트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/")
def index():
    return render_template_string(HTML, oda=bool(ODA))

@app.route("/convert", methods=["POST"])
def api_convert():
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        return jsonify(success=False, error="파일이 없습니다.")

    paper = request.form.get("paper_size", "a3")
    bg = request.form.get("bg_color", "white")
    dpi = max(72, min(600, int(request.form.get("dpi", "300"))))
    batch = uuid.uuid4().hex[:8]
    results = []

    for file in files:
        if not file.filename: continue
        ext = Path(file.filename).suffix.lower()
        if ext not in {".dwg", ".dxf"}:
            results.append({"name": file.filename, "ok": False, "error": f"지원하지 않는 형식: {ext}"}); continue
        if ext == ".dwg" and not ODA:
            results.append({"name": file.filename, "ok": False, "error": "ODA 미설치 — DXF로 내보내기 후 업로드하세요"}); continue

        uid = uuid.uuid4().hex[:8]
        inp = UPLOAD_DIR / f"{uid}{ext}"; file.save(str(inp))
        pdf_name = Path(file.filename).stem + ".pdf"
        out_name = f"{uid}_{pdf_name}"; out = OUTPUT_DIR / out_name
        try:
            convert_one(str(inp), str(out), paper, bg, dpi)
            results.append({"name": file.filename, "ok": True, "pdf": pdf_name, "url": f"/download/{out_name}"})
        except Exception as e:
            results.append({"name": file.filename, "ok": False, "error": str(e)})
        finally:
            inp.unlink(missing_ok=True)

    ok = [r for r in results if r.get("ok")]
    zip_url = None
    if len(ok) >= 2:
        zn = f"{batch}_converted.zip"; zp = OUTPUT_DIR / zn
        with zipfile.ZipFile(str(zp), "w", zipfile.ZIP_DEFLATED) as zf:
            for r in ok:
                fp = OUTPUT_DIR / r["url"].split("/")[-1]
                if fp.exists(): zf.write(str(fp), r["pdf"])
        zip_url = f"/download/{zn}"

    return jsonify(success=True, results=results, zip_url=zip_url)

@app.route("/download/<filename>")
def download(filename):
    fp = OUTPUT_DIR / filename
    if not fp.exists(): return jsonify(success=False, error="파일 없음"), 404
    orig = "_".join(filename.split("_")[1:]) if "_" in filename else filename
    return send_file(str(fp), as_attachment=True, download_name=orig)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>정수산업개발 — 도면 변환</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700;900&display=swap');
:root{
  --bg:#08080e;--sf:#111119;--sf2:#19192a;--bd:#252545;--bh:#383860;
  --tx:#e8e8f4;--dim:#7a7a9e;
  --ac:#2d7cf6;--ac2:#6c5ce7;--acg:rgba(45,124,246,.12);--acs:rgba(45,124,246,.3);
  --ok:#2dd4a0;--err:#f87171;--warn:#fbbf24;--r:14px;
}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Noto Sans KR',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh;display:flex;justify-content:center;-webkit-font-smoothing:antialiased;line-height:1.5}

/* 배경 장식 */
body::before{content:'';position:fixed;top:-20vh;left:50%;transform:translateX(-50%);width:80vw;height:60vh;background:radial-gradient(ellipse,rgba(45,124,246,.06)0%,rgba(108,92,231,.04)40%,transparent 70%);pointer-events:none}

.w{position:relative;width:100%;max-width:620px;padding:2rem 1.25rem 3rem}

/* ── 브랜드 헤더 ── */
.brand{text-align:center;margin-bottom:.6rem}
.brand-name{
  font-size:1.1rem;font-weight:900;letter-spacing:.15em;
  text-transform:uppercase;
  background:linear-gradient(135deg,#e8e8f4 0%,#a0a0c0 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  opacity:.9;
}
.brand-bar{
  width:40px;height:2px;margin:.5rem auto 0;
  background:linear-gradient(90deg,var(--ac),var(--ac2));
  border-radius:1px;
}

/* ── 메인 헤더 ── */
.hd{text-align:center;margin-bottom:1.8rem}
.hd .tag{display:inline-flex;align-items:center;gap:.45rem;background:var(--sf2);border:1px solid var(--bd);border-radius:100px;padding:.25rem .8rem;font-size:.68rem;font-weight:500;color:var(--dim);margin-bottom:.8rem}
.hd .tag .d{width:6px;height:6px;border-radius:50%;background:var(--ok);box-shadow:0 0 6px var(--ok)}
.hd h1{font-size:1.5rem;font-weight:700;letter-spacing:-.03em}
.hd h1 em{font-style:normal;background:linear-gradient(135deg,var(--ac),var(--ac2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hd p{color:var(--dim);font-size:.8rem;margin-top:.25rem}

/* ── 드롭존 ── */
.dp{position:relative;border:1.5px dashed var(--bd);border-radius:var(--r);padding:2rem 1.5rem;text-align:center;cursor:pointer;transition:all .25s;background:var(--sf)}
.dp:hover,.dp.ov{border-color:var(--ac);background:rgba(45,124,246,.03);box-shadow:0 0 48px var(--acg)}
.dp .ic{font-size:2rem;margin-bottom:.5rem;opacity:.5;transition:transform .3s}
.dp:hover .ic{transform:translateY(-3px);opacity:.8}
.dp h3{font-size:.88rem;font-weight:500}
.dp p{color:var(--dim);font-size:.72rem;margin-top:.25rem}
.dp input{position:absolute;inset:0;opacity:0;cursor:pointer}

/* ── 파일 리스트 ── */
.fl{margin-top:.7rem;display:flex;flex-direction:column;gap:.35rem}
.fl:empty{display:none}
.fi{display:flex;align-items:center;gap:.55rem;background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:.5rem .75rem;animation:fi .2s ease}
@keyframes fi{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.fi .badge{flex-shrink:0;width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--ac),var(--ac2));display:flex;align-items:center;justify-content:center;font-size:.58rem;font-weight:700;color:#fff}
.fi .meta{flex:1;min-width:0}
.fi .fn{font-size:.76rem;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fi .fs{font-size:.66rem;color:var(--dim)}
.fi .st{flex-shrink:0;font-size:.64rem;font-weight:500;padding:.12rem .45rem;border-radius:5px;display:none}
.fi .st.wait{display:block;background:rgba(124,124,154,.08);color:var(--dim)}
.fi .st.run{display:block;background:var(--acg);color:var(--ac)}
.fi .st.done{display:block;background:rgba(45,212,160,.1);color:var(--ok)}
.fi .st.fail{display:block;background:rgba(248,113,113,.1);color:var(--err)}
.fi .x{flex-shrink:0;background:none;border:none;color:var(--dim);cursor:pointer;font-size:.95rem;padding:2px 5px;border-radius:6px;transition:all .2s}
.fi .x:hover{color:var(--err);background:rgba(248,113,113,.08)}

.fc{margin-top:.4rem;font-size:.7rem;color:var(--dim);text-align:right}
.fc .ca{background:none;border:none;color:var(--err);font-size:.7rem;cursor:pointer;opacity:.7;transition:opacity .2s}
.fc .ca:hover{opacity:1}

/* ── 옵션 ── */
.op{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.7rem;margin-top:1rem}
.og{display:flex;flex-direction:column;gap:.25rem}
.og label{font-size:.65rem;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.08em}
.og select{appearance:none;background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:.5rem .7rem;color:var(--tx);font-family:inherit;font-size:.8rem;cursor:pointer;transition:border-color .2s;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M2.5 4l2.5 2.5L7.5 4' fill='none' stroke='%237a7a9e' stroke-width='1.4'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right .6rem center;padding-right:1.8rem}
.og select:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--acg)}

/* ── 버튼 ── */
.bt{width:100%;margin-top:1rem;padding:.75rem;border:none;border-radius:12px;background:linear-gradient(135deg,var(--ac),var(--ac2));color:#fff;font-family:inherit;font-size:.88rem;font-weight:600;cursor:pointer;transition:all .25s;box-shadow:0 4px 20px var(--acg);display:flex;align-items:center;justify-content:center;gap:.5rem}
.bt:hover:not(:disabled){box-shadow:0 6px 28px var(--acs);transform:translateY(-1px)}
.bt:disabled{opacity:.4;cursor:not-allowed}
.bt .sp{display:none;width:15px;height:15px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:sp .7s linear infinite}
.bt.ld .sp{display:block}.bt.ld .lb{display:none}.bt.ld .ll{display:inline}
.ll{display:none}
@keyframes sp{to{transform:rotate(360deg)}}

/* ── 프로그레스 ── */
.pgw{display:none;margin-top:.6rem}.pgw.show{display:block}
.pgb{height:3px;border-radius:2px;background:var(--sf2);overflow:hidden}
.pgf{height:100%;width:0%;background:linear-gradient(90deg,var(--ac),var(--ac2));border-radius:2px;transition:width .3s}
.pgt{font-size:.68rem;color:var(--dim);margin-top:.25rem;text-align:center}

/* ── 결과 ── */
.rs{display:none;margin-top:.8rem;padding:.8rem 1rem;border-radius:12px;font-size:.8rem;border:1px solid}
.rs.sh{display:block}
.rs.ok{background:rgba(45,212,160,.06);border-color:rgba(45,212,160,.2);color:var(--ok)}
.rs.er{background:rgba(248,113,113,.06);border-color:rgba(248,113,113,.2);color:var(--err);white-space:pre-wrap}
.rs .dl{display:flex;flex-direction:column;gap:.3rem;margin-top:.5rem}
.rs a{display:inline-flex;align-items:center;gap:.3rem;padding:.38rem .75rem;background:var(--ok);color:#0c0c12;text-decoration:none;border-radius:8px;font-weight:600;font-size:.72rem;transition:opacity .2s}
.rs a:hover{opacity:.85}
.rs a.zip{background:linear-gradient(135deg,var(--ac),var(--ac2));color:#fff}
.rs .fail-item{color:var(--err);font-size:.72rem;margin-top:.15rem}

/* ── 안내 ── */
.nf{margin-top:1.4rem;padding:.75rem .9rem;background:var(--sf);border:1px solid var(--bd);border-radius:12px;font-size:.7rem;color:var(--dim);line-height:1.7}
.nf strong{color:var(--warn);font-weight:600;display:block;margin-bottom:.2rem}
.nf .ow{color:var(--err);margin-top:.3rem;font-weight:500}
.nf a{color:#6fa4ff}

.ft{margin-top:2rem;text-align:center;font-size:.62rem;color:var(--dim);opacity:.3}
@media(max-width:480px){.op{grid-template-columns:1fr 1fr}.hd h1{font-size:1.25rem}.brand-name{font-size:.95rem}}
</style>
</head>
<body>
<div class="w">

  <div class="brand">
    <div class="brand-name">정수산업개발</div>
    <div class="brand-bar"></div>
  </div>

  <div class="hd">
    <div class="tag"><span class="d"></span>DWG · DXF · 한글 폰트</div>
    <h1>도면 <em>→ PDF</em> 변환기</h1>
    <p>여러 파일을 한 번에 고품질 PDF로 변환합니다</p>
  </div>

  <div class="dp" id="dp">
    <div class="ic">&#128209;</div>
    <h3>파일을 드래그하거나 클릭</h3>
    <p>.dwg, .dxf · 여러 파일 선택 가능 · 최대 100MB/개</p>
    <input type="file" id="inp" accept=".dwg,.dxf,.DWG,.DXF" multiple>
  </div>

  <div class="fl" id="fl"></div>
  <div class="fc" id="fc" style="display:none">
    <span id="fcT"></span>
    <button class="ca" id="ca">전체 삭제</button>
  </div>

  <div class="op">
    <div class="og"><label>용지</label><select id="pp">
      <option value="a4">A4</option><option value="a3" selected>A3</option>
      <option value="a2">A2</option><option value="a1">A1</option><option value="a0">A0</option>
    </select></div>
    <div class="og"><label>배경</label><select id="bg">
      <option value="white" selected>흰색</option><option value="black">검정</option>
    </select></div>
    <div class="og"><label>해상도</label><select id="dpi">
      <option value="72">72 DPI</option><option value="150">150 DPI</option><option value="300" selected>300 DPI</option>
    </select></div>
  </div>

  <button class="bt" id="bt" disabled>
    <span class="sp"></span><span class="lb">PDF로 변환</span><span class="ll">변환 중…</span>
  </button>

  <div class="pgw" id="pgw"><div class="pgb"><div class="pgf" id="pgf"></div></div><div class="pgt" id="pgt"></div></div>
  <div class="rs" id="rs"></div>

  <div class="nf">
    <strong>&#9888;&#65039; 참고</strong>
    한글 SHX 폰트(whgtxt, korgot 등)는 시스템 한글 폰트로 자동 대체됩니다.
    여러 파일 시 ZIP 묶음 다운로드를 지원합니다.
    {% if not oda %}
    <div class="ow">&#9888; DWG 변환에는 ODA File Converter 필요 — 현재 DXF만 가능</div>
    {% endif %}
  </div>
  <div class="ft">정수산업개발 · 도면 변환 시스템</div>
</div>

<script>
const $=s=>document.getElementById(s);
const dp=$('dp'),inp=$('inp'),fl=$('fl'),fc=$('fc'),fcT=$('fcT'),ca=$('ca'),
      bt=$('bt'),rs=$('rs'),pgw=$('pgw'),pgf=$('pgf'),pgt=$('pgt');
let files=[],fid=0;
const fmt=b=>b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':(b/1048576).toFixed(1)+' MB';

function addFile(f){
  const ext=f.name.split('.').pop().toLowerCase();
  if(!['dwg','dxf'].includes(ext))return;
  if(f.size>104857600)return;
  if(files.some(x=>x.file.name===f.name&&x.file.size===f.size))return;
  const id=fid++;
  const el=document.createElement('div');el.className='fi';
  el.innerHTML=`<div class="badge">${ext.toUpperCase()}</div><div class="meta"><div class="fn">${f.name}</div><div class="fs">${fmt(f.size)}</div></div><div class="st wait" data-st="st_${id}">대기</div><button class="x" data-id="${id}">✕</button>`;
  el.querySelector('.x').onclick=()=>rmFile(id);
  fl.appendChild(el);files.push({id,file:f,el});upd();
}
function rmFile(id){const i=files.findIndex(x=>x.id===id);if(i>=0){files[i].el.remove();files.splice(i,1)}upd()}
function clrAll(){files.forEach(x=>x.el.remove());files=[];upd()}
function upd(){bt.disabled=!files.length;fc.style.display=files.length?'block':'none';fcT.textContent=files.length+'개 파일';hd()}
function setSt(id,cls,txt){const e=document.querySelector(`[data-st="st_${id}"]`);if(e){e.className='st '+cls;e.textContent=txt}}
function sh(t,h){rs.className='rs sh '+t;rs.innerHTML=h}
function hd(){rs.className='rs';rs.innerHTML=''}

['dragenter','dragover'].forEach(e=>dp.addEventListener(e,ev=>{ev.preventDefault();dp.classList.add('ov')}));
['dragleave','drop'].forEach(e=>dp.addEventListener(e,ev=>{ev.preventDefault();dp.classList.remove('ov')}));
dp.addEventListener('drop',ev=>{[...ev.dataTransfer.files].forEach(addFile)});
inp.addEventListener('change',()=>{[...inp.files].forEach(addFile);inp.value=''});
ca.addEventListener('click',clrAll);

bt.addEventListener('click',async()=>{
  if(!files.length)return;
  bt.classList.add('ld');bt.disabled=true;hd();
  pgw.classList.add('show');pgf.style.width='0%';
  files.forEach(f=>setSt(f.id,'run','변환 중'));
  pgt.textContent=`0 / ${files.length}`;

  const fd=new FormData();
  files.forEach(f=>fd.append('files',f.file));
  fd.append('paper_size',$('pp').value);fd.append('bg_color',$('bg').value);fd.append('dpi',$('dpi').value);

  try{
    const r=await fetch('/convert',{method:'POST',body:fd}),d=await r.json();
    if(!d.success){sh('er','❌ '+d.error);files.forEach(f=>setSt(f.id,'fail','실패'))}
    else{
      let ok=0,fail=0;const dls=[],errs=[];
      d.results.forEach((res,i)=>{
        const fo=files[i];if(!fo)return;
        if(res.ok){ok++;setSt(fo.id,'done','완료');dls.push(`<a href="${res.url}" download>⬇ ${res.pdf}</a>`)}
        else{fail++;setSt(fo.id,'fail','실패');errs.push(`<div class="fail-item">❌ ${res.name}: ${res.error}</div>`)}
        pgf.style.width=((i+1)/d.results.length*100).toFixed(0)+'%';
        pgt.textContent=`${i+1} / ${d.results.length}`;
      });
      let h='';
      if(ok){h+=`✅ ${ok}개 변환 완료`;h+='<div class="dl">';if(d.zip_url)h+=`<a class="zip" href="${d.zip_url}" download>📦 전체 ZIP (${ok}개)</a>`;h+=dls.join('')+'</div>'}
      if(fail)h+=errs.join('');
      sh(fail&&!ok?'er':'ok',h);
    }
  }catch(e){sh('er','❌ '+e.message);files.forEach(f=>setSt(f.id,'fail','오류'))}
  pgf.style.width='100%';bt.classList.remove('ld');bt.disabled=false;
});
</script>
</body></html>"""


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  정수산업개발 — 도면 변환기")
    print(f"  한글 폰트: {KO_NAME or '미발견'}")
    print(f"  ODA: {'✓ '+ODA if ODA else '✗ (DXF만)'}")
    print("  http://localhost:5000")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)