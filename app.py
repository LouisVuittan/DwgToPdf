#!/usr/bin/env python3
"""
DWG/DXF → PDF 변환 웹 서비스 (다중 파일 지원)
- DXF: ezdxf + matplotlib 렌더링
- DWG: ODA File Converter → DXF → PDF
- 한글 SHX 폰트 → 시스템 한글 폰트 매핑
"""

import os
import sys
import uuid
import time
import shutil
import zipfile
import platform
import subprocess
import threading
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
#  한글 폰트 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def setup_korean_fonts():
    is_win = platform.system() == "Windows"
    keywords = [
        "Malgun Gothic", "맑은 고딕", "Gulim", "Batang", "Dotum",
        "NanumGothic", "Nanum Gothic", "NanumMyeongjo",
        "Noto Sans CJK KR", "Noto Sans KR",
        "AppleGothic", "Apple SD Gothic Neo",
    ]
    found_path = found_name = None

    for fi in fm.fontManager.ttflist:
        for kw in keywords:
            if kw.lower() in fi.name.lower():
                found_path, found_name = fi.fname, fi.name
                break
        if found_path:
            break

    if not found_path and is_win:
        wf = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for fn, dn in [("malgun.ttf","Malgun Gothic"),("malgunbd.ttf","Malgun Gothic"),("gulim.ttc","Gulim")]:
            fp = wf / fn
            if fp.exists():
                found_path, found_name = str(fp), dn
                fm.fontManager.addfont(str(fp))
                break

    if not found_path:
        for p in [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        ]:
            if os.path.isfile(p):
                fm.fontManager.addfont(p)
                found_path, found_name = p, "Noto Sans CJK KR"
                break

    if found_path:
        print(f"[폰트] {found_name} ({found_path})")
        mpl.rcParams["font.sans-serif"] = [found_name] + mpl.rcParams["font.sans-serif"]
        mpl.rcParams["axes.unicode_minus"] = False
    else:
        print("[폰트] ⚠ 한글 폰트 미발견")

    ttf = os.path.basename(found_path) if found_path else "malgun.ttf"
    shx_map = {}
    for n in ["WHGTXT","WHGDTXT","WHGGTXT","WHGRTXT","KORGOT","KORGOTB","KORGT","KORGTB","WHTGTXT","WHTMTXT"]:
        shx_map[n] = ttf
        shx_map[n + ".SHX"] = ttf
    ezdxf_fonts.SHX_FONTS.update(shx_map)
    print(f"[폰트] SHX 매핑 → {ttf}")
    return found_path, found_name

KOREAN_FONT_PATH, KOREAN_FONT_NAME = setup_korean_fonts()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ODA File Converter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def find_oda():
    candidates = []
    if platform.system() == "Windows":
        for base in [os.environ.get("ProgramFiles",""), os.environ.get("ProgramFiles(x86)",""), os.environ.get("LOCALAPPDATA","")]:
            if base:
                od = Path(base) / "ODA"
                if od.exists():
                    for sub in sorted(od.iterdir(), reverse=True):
                        e = sub / "ODAFileConverter.exe"
                        if e.exists(): candidates.append(str(e))
    else:
        candidates += ["/usr/bin/ODAFileConverter", "/usr/local/bin/ODAFileConverter"]
    for c in candidates:
        if os.path.isfile(c): return c
    return shutil.which("ODAFileConverter")

ODA_PATH = find_oda()
print(f"[ODA] {'✓ ' + ODA_PATH if ODA_PATH else '✗ DXF만 가능'}")


def dwg_to_dxf(dwg_path, out_dir):
    if not ODA_PATH:
        raise RuntimeError("DWG 변환에는 ODA File Converter 필요\nhttps://www.opendesign.com/guestfiles/oda_file_converter")
    subprocess.run([ODA_PATH, str(Path(dwg_path).parent), out_dir, "ACAD2018", "DXF", "0", "1", Path(dwg_path).name],
                   capture_output=True, text=True, timeout=120)
    dxf = Path(out_dir) / (Path(dwg_path).stem + ".dxf")
    if not dxf.exists(): raise RuntimeError("DWG → DXF 변환 실패")
    return str(dxf)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  변환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PAPER = {"a4":(8.27,11.69),"a3":(11.69,16.54),"a2":(16.54,23.39),"a1":(23.39,33.11),"a0":(33.11,46.81)}

def dxf_to_pdf(dxf_path, pdf_path, paper="a3", bg="white", dpi=150):
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

def convert_one(inp, pdf, paper="a3", bg="white", dpi=150):
    ext = Path(inp).suffix.lower()
    if ext == ".dwg":
        tmp = str(UPLOAD_DIR / f"oda_{uuid.uuid4().hex[:8]}")
        os.makedirs(tmp, exist_ok=True)
        try:
            dxf_to_pdf(dwg_to_dxf(inp, tmp), pdf, paper, bg, dpi)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    elif ext == ".dxf":
        dxf_to_pdf(inp, pdf, paper, bg, dpi)
    else:
        raise ValueError(f"지원하지 않는 형식: {ext}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  자동 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cleanup():
    while True:
        time.sleep(600)
        now = time.time()
        for d in [UPLOAD_DIR, OUTPUT_DIR]:
            if not d.exists(): continue
            for f in d.iterdir():
                if f.is_file() and now - f.stat().st_mtime > 1800:
                    f.unlink(missing_ok=True)
threading.Thread(target=cleanup, daemon=True).start()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  라우트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/")
def index():
    return render_template_string(HTML, oda=bool(ODA_PATH))

@app.route("/convert", methods=["POST"])
def api_convert():
    """다중 파일 변환. 각 파일 결과를 배열로 반환."""
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        return jsonify(success=False, error="파일이 없습니다.")

    paper = request.form.get("paper_size", "a3")
    bg = request.form.get("bg_color", "white")
    dpi = max(72, min(600, int(request.form.get("dpi", "150"))))

    batch_id = uuid.uuid4().hex[:8]
    results = []

    for file in files:
        if not file.filename:
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in {".dwg", ".dxf"}:
            results.append({"name": file.filename, "ok": False, "error": f"지원하지 않는 형식: {ext}"})
            continue
        if ext == ".dwg" and not ODA_PATH:
            results.append({"name": file.filename, "ok": False, "error": "DWG 변환에는 ODA File Converter 필요"})
            continue

        uid = uuid.uuid4().hex[:8]
        inp = UPLOAD_DIR / f"{uid}{ext}"
        file.save(str(inp))

        pdf_name = Path(file.filename).stem + ".pdf"
        out_name = f"{uid}_{pdf_name}"
        out = OUTPUT_DIR / out_name

        try:
            convert_one(str(inp), str(out), paper, bg, dpi)
            results.append({"name": file.filename, "ok": True, "pdf": pdf_name, "url": f"/download/{out_name}"})
        except Exception as e:
            results.append({"name": file.filename, "ok": False, "error": str(e)})
        finally:
            inp.unlink(missing_ok=True)

    # 성공한 파일이 2개 이상이면 ZIP도 생성
    ok_files = [r for r in results if r.get("ok")]
    zip_url = None
    if len(ok_files) >= 2:
        zip_name = f"{batch_id}_converted.zip"
        zip_path = OUTPUT_DIR / zip_name
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for r in ok_files:
                # url에서 파일명 추출
                fname = r["url"].split("/")[-1]
                fpath = OUTPUT_DIR / fname
                if fpath.exists():
                    zf.write(str(fpath), r["pdf"])
        zip_url = f"/download/{zip_name}"

    return jsonify(success=True, results=results, zip_url=zip_url)

@app.route("/download/<filename>")
def download(filename):
    fp = OUTPUT_DIR / filename
    if not fp.exists():
        return jsonify(success=False, error="파일 없음"), 404
    orig = "_".join(filename.split("_")[1:]) if "_" in filename else filename
    return send_file(str(fp), as_attachment=True, download_name=orig)

@app.route("/status")
def status():
    return jsonify(oda=bool(ODA_PATH), font=KOREAN_FONT_NAME)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DWG/DXF → PDF 변환기</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
:root{
  --bg:#0c0c12;--sf:#141420;--sf2:#1b1b2a;--bd:#262640;--bh:#3a3a5c;
  --tx:#e4e4ef;--dim:#7c7c9a;--ac:#4f8ff7;--acg:rgba(79,143,247,.15);--acs:rgba(79,143,247,.3);
  --ok:#34d399;--err:#f87171;--warn:#fbbf24;--r:14px;
}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Noto Sans KR',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh;display:flex;justify-content:center;line-height:1.5;-webkit-font-smoothing:antialiased}
body::before{content:'';position:fixed;top:-30vh;left:-10vw;width:50vw;height:50vw;background:radial-gradient(circle,rgba(79,143,247,.07)0%,transparent 65%);pointer-events:none}
.w{position:relative;width:100%;max-width:600px;padding:2.5rem 1.25rem 3rem}

/* header */
.hd{text-align:center;margin-bottom:2rem}
.hd .tag{display:inline-flex;align-items:center;gap:.45rem;background:var(--sf2);border:1px solid var(--bd);border-radius:100px;padding:.3rem .9rem;font-size:.72rem;font-weight:500;color:var(--dim);margin-bottom:1rem}
.hd .tag .d{width:6px;height:6px;border-radius:50%;background:var(--ok);box-shadow:0 0 6px var(--ok)}
.hd h1{font-size:1.6rem;font-weight:700;letter-spacing:-.03em}
.hd h1 em{font-style:normal;background:linear-gradient(135deg,var(--ac),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hd p{color:var(--dim);font-size:.82rem;margin-top:.3rem}

/* dropzone */
.dp{position:relative;border:1.5px dashed var(--bd);border-radius:var(--r);padding:2.2rem 1.5rem;text-align:center;cursor:pointer;transition:all .25s;background:var(--sf)}
.dp:hover,.dp.ov{border-color:var(--ac);background:rgba(79,143,247,.03);box-shadow:0 0 48px var(--acg)}
.dp .ic{font-size:2rem;margin-bottom:.6rem;opacity:.5;transition:transform .3s}
.dp:hover .ic{transform:translateY(-3px);opacity:.8}
.dp h3{font-size:.9rem;font-weight:500}
.dp p{color:var(--dim);font-size:.75rem;margin-top:.3rem}
.dp input{position:absolute;inset:0;opacity:0;cursor:pointer}

/* file list */
.fl{margin-top:.8rem;display:flex;flex-direction:column;gap:.4rem}
.fl:empty{display:none}
.fi{display:flex;align-items:center;gap:.6rem;background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:.55rem .8rem;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}
.fi .badge{flex-shrink:0;width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,var(--ac),#7c3aed);display:flex;align-items:center;justify-content:center;font-size:.6rem;font-weight:700;color:#fff;letter-spacing:.02em}
.fi .meta{flex:1;min-width:0}
.fi .fn{font-size:.78rem;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fi .fs{font-size:.68rem;color:var(--dim)}
.fi .st{flex-shrink:0;font-size:.68rem;font-weight:500;padding:.15rem .5rem;border-radius:6px;display:none}
.fi .st.wait{display:block;background:rgba(124,124,154,.1);color:var(--dim)}
.fi .st.run{display:block;background:var(--acg);color:var(--ac)}
.fi .st.done{display:block;background:rgba(52,211,153,.1);color:var(--ok)}
.fi .st.fail{display:block;background:rgba(248,113,113,.1);color:var(--err)}
.fi .x{flex-shrink:0;background:none;border:none;color:var(--dim);cursor:pointer;font-size:1rem;padding:2px 5px;border-radius:6px;transition:all .2s}
.fi .x:hover{color:var(--err);background:rgba(248,113,113,.08)}

.file-count{margin-top:.5rem;font-size:.72rem;color:var(--dim);text-align:right}
.clear-all{background:none;border:none;color:var(--err);font-size:.72rem;cursor:pointer;margin-left:.5rem;opacity:.7;transition:opacity .2s}
.clear-all:hover{opacity:1}

/* options */
.op{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.8rem;margin-top:1rem}
.og{display:flex;flex-direction:column;gap:.3rem}
.og label{font-size:.68rem;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}
.og select{appearance:none;background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:.55rem .75rem;color:var(--tx);font-family:inherit;font-size:.82rem;cursor:pointer;transition:border-color .2s;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'%3E%3Cpath d='M2.5 4l2.5 2.5L7.5 4' fill='none' stroke='%237c7c9a' stroke-width='1.4'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right .6rem center;padding-right:1.8rem}
.og select:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px var(--acg)}

/* button */
.bt{width:100%;margin-top:1.2rem;padding:.8rem;border:none;border-radius:12px;background:linear-gradient(135deg,var(--ac),#7c3aed);color:#fff;font-family:inherit;font-size:.9rem;font-weight:600;cursor:pointer;transition:all .25s;box-shadow:0 4px 20px var(--acg);display:flex;align-items:center;justify-content:center;gap:.5rem}
.bt:hover:not(:disabled){box-shadow:0 6px 28px var(--acs);transform:translateY(-1px)}
.bt:disabled{opacity:.4;cursor:not-allowed}
.bt .sp{display:none;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:sp .7s linear infinite}
.bt.ld .sp{display:block}.bt.ld .lb{display:none}.bt.ld .ll{display:inline}
.ll{display:none}
@keyframes sp{to{transform:rotate(360deg)}}

/* progress bar */
.pg-wrap{display:none;margin-top:.8rem}
.pg-wrap.show{display:block}
.pg-bar{height:4px;border-radius:2px;background:var(--sf2);overflow:hidden}
.pg-fill{height:100%;width:0%;background:linear-gradient(90deg,var(--ac),#a78bfa);border-radius:2px;transition:width .3s ease}
.pg-text{font-size:.7rem;color:var(--dim);margin-top:.3rem;text-align:center}

/* result area */
.rs{display:none;margin-top:1rem;padding:.9rem 1rem;border-radius:12px;font-size:.82rem;border:1px solid}
.rs.sh{display:block}
.rs.ok{background:rgba(52,211,153,.08);border-color:rgba(52,211,153,.2);color:var(--ok)}
.rs.er{background:rgba(248,113,113,.08);border-color:rgba(248,113,113,.2);color:var(--err);white-space:pre-wrap}
.rs .dl-list{display:flex;flex-direction:column;gap:.35rem;margin-top:.6rem}
.rs a{display:inline-flex;align-items:center;gap:.3rem;padding:.4rem .8rem;background:var(--ok);color:#0c0c12;text-decoration:none;border-radius:8px;font-weight:600;font-size:.75rem;transition:opacity .2s}
.rs a:hover{opacity:.85}
.rs a.zip{background:var(--ac)}
.rs .fail-item{color:var(--err);font-size:.75rem;margin-top:.2rem}

/* info */
.nf{margin-top:1.5rem;padding:.85rem 1rem;background:var(--sf);border:1px solid var(--bd);border-radius:12px;font-size:.73rem;color:var(--dim);line-height:1.7}
.nf strong{color:var(--warn);font-weight:600;display:block;margin-bottom:.3rem}
.nf .ow{color:var(--err);margin-top:.4rem;font-weight:500}
.nf a{color:#6fa4ff}
.ft{margin-top:2rem;text-align:center;font-size:.65rem;color:var(--dim);opacity:.4}

@media(max-width:480px){.op{grid-template-columns:1fr 1fr}.hd h1{font-size:1.35rem}}
</style>
</head>
<body>
<div class="w">
  <div class="hd">
    <div class="tag"><span class="d"></span>다중 파일 · 한글 폰트 지원</div>
    <h1>DWG/DXF <em>→ PDF</em></h1>
    <p>여러 도면 파일을 한 번에 PDF로 변환합니다</p>
  </div>

  <div class="dp" id="dp">
    <div class="ic">&#128209;</div>
    <h3>파일을 드래그하거나 클릭</h3>
    <p>.dwg, .dxf · 여러 파일 선택 가능 · 최대 100MB/개</p>
    <input type="file" id="inp" accept=".dwg,.dxf,.DWG,.DXF" multiple>
  </div>

  <div class="fl" id="fl"></div>
  <div class="file-count" id="fc" style="display:none">
    <span id="fcText"></span>
    <button class="clear-all" id="ca">전체 삭제</button>
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
      <option value="72">72 DPI</option><option value="150" selected>150 DPI</option><option value="300">300 DPI</option>
    </select></div>
  </div>

  <button class="bt" id="bt" disabled>
    <span class="sp"></span><span class="lb">PDF로 변환</span><span class="ll">변환 중…</span>
  </button>

  <div class="pg-wrap" id="pgw">
    <div class="pg-bar"><div class="pg-fill" id="pgf"></div></div>
    <div class="pg-text" id="pgt"></div>
  </div>

  <div class="rs" id="rs"></div>

  <div class="nf">
    <strong>&#9888;&#65039; 참고사항</strong>
    한글 SHX 폰트(whgtxt, korgot 등)는 시스템의 맑은 고딕/나눔고딕 등으로 자동 대체됩니다.
    여러 파일 변환 시 ZIP으로 묶어서 다운로드할 수 있습니다.
    {% if not oda %}
    <div class="ow">&#9888; DWG 변환에는 <a href="https://www.opendesign.com/guestfiles/oda_file_converter" target="_blank">ODA File Converter</a>(무료) 필요 — 현재 DXF만 가능</div>
    {% endif %}
  </div>
  <div class="ft">ezdxf + matplotlib</div>
</div>

<script>
const $=s=>document.getElementById(s);
const dp=$('dp'),inp=$('inp'),fl=$('fl'),fc=$('fc'),fcText=$('fcText'),ca=$('ca'),
      bt=$('bt'),rs=$('rs'),pgw=$('pgw'),pgf=$('pgf'),pgt=$('pgt');

let files = []; // {id, file, el}

const fmt=b=>b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':(b/1048576).toFixed(1)+' MB';
let fid=0;

function addFile(f) {
  const ext=f.name.split('.').pop().toLowerCase();
  if(!['dwg','dxf'].includes(ext)){return}
  if(f.size>104857600){return}
  // 중복 체크
  if(files.some(x=>x.file.name===f.name && x.file.size===f.size)){return}

  const id=fid++;
  const el=document.createElement('div');
  el.className='fi';
  el.innerHTML=`
    <div class="badge">${ext.toUpperCase()}</div>
    <div class="meta"><div class="fn">${f.name}</div><div class="fs">${fmt(f.size)}</div></div>
    <div class="st wait" data-st="st_${id}">대기</div>
    <button class="x" data-id="${id}">✕</button>`;
  el.querySelector('.x').onclick=()=>removeFile(id);
  fl.appendChild(el);
  files.push({id, file:f, el});
  updateUI();
}

function removeFile(id) {
  const idx=files.findIndex(x=>x.id===id);
  if(idx>=0){files[idx].el.remove();files.splice(idx,1)}
  updateUI();
}

function clearAll() {
  files.forEach(x=>x.el.remove());
  files=[];
  updateUI();
}

function updateUI() {
  bt.disabled=files.length===0;
  if(files.length>0){
    fc.style.display='block';
    fcText.textContent=`${files.length}개 파일`;
  } else {
    fc.style.display='none';
  }
  hideResult();
}

function setStatus(id, cls, text) {
  const el=document.querySelector(`[data-st="st_${id}"]`);
  if(!el)return;
  el.className='st '+cls;
  el.textContent=text;
}

function showResult(type, html){rs.className='rs sh '+type;rs.innerHTML=html}
function hideResult(){rs.className='rs';rs.innerHTML=''}

// drag & drop
['dragenter','dragover'].forEach(e=>dp.addEventListener(e,ev=>{ev.preventDefault();dp.classList.add('ov')}));
['dragleave','drop'].forEach(e=>dp.addEventListener(e,ev=>{ev.preventDefault();dp.classList.remove('ov')}));
dp.addEventListener('drop',ev=>{
  [...ev.dataTransfer.files].forEach(addFile);
});
inp.addEventListener('change',()=>{
  [...inp.files].forEach(addFile);
  inp.value='';
});
ca.addEventListener('click',clearAll);

// convert
bt.addEventListener('click', async()=>{
  if(!files.length)return;
  bt.classList.add('ld');bt.disabled=true;hideResult();
  pgw.classList.add('show');
  pgf.style.width='0%';

  // 모든 파일 상태 초기화
  files.forEach(f=>setStatus(f.id,'run','변환 중'));
  pgt.textContent=`0 / ${files.length} 변환 중…`;

  const fd=new FormData();
  files.forEach(f=>fd.append('files',f.file));
  fd.append('paper_size',$('pp').value);
  fd.append('bg_color',$('bg').value);
  fd.append('dpi',$('dpi').value);

  try {
    const r=await fetch('/convert',{method:'POST',body:fd});
    const d=await r.json();

    if(!d.success){
      showResult('er','❌ '+d.error);
      files.forEach(f=>setStatus(f.id,'fail','실패'));
    } else {
      let okCount=0, failCount=0;
      const dlLinks=[];
      const failMsgs=[];

      d.results.forEach((res,i)=>{
        const fObj=files[i];
        if(!fObj)return;
        if(res.ok){
          okCount++;
          setStatus(fObj.id,'done','완료');
          dlLinks.push(`<a href="${res.url}" download>⬇ ${res.pdf}</a>`);
        } else {
          failCount++;
          setStatus(fObj.id,'fail','실패');
          failMsgs.push(`<div class="fail-item">❌ ${res.name}: ${res.error}</div>`);
        }
        pgf.style.width=`${((i+1)/d.results.length*100).toFixed(0)}%`;
        pgt.textContent=`${i+1} / ${d.results.length} 완료`;
      });

      let html='';
      if(okCount>0){
        html+=`✅ ${okCount}개 파일 변환 완료!`;
        html+='<div class="dl-list">';
        if(d.zip_url){
          html+=`<a class="zip" href="${d.zip_url}" download>📦 전체 ZIP 다운로드 (${okCount}개)</a>`;
        }
        html+=dlLinks.join('');
        html+='</div>';
      }
      if(failCount>0){
        html+=failMsgs.join('');
      }
      showResult(failCount>0&&okCount===0?'er':'ok', html);
    }
  } catch(e) {
    showResult('er','❌ 서버 오류: '+e.message);
    files.forEach(f=>setStatus(f.id,'fail','오류'));
  }

  pgf.style.width='100%';
  bt.classList.remove('ld');bt.disabled=false;
});
</script>
</body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 50)
    print("  DWG/DXF → PDF 변환기 (다중 파일)")
    print(f"  한글 폰트: {KOREAN_FONT_NAME or '미발견'}")
    print(f"  ODA: {'✓ ' + (ODA_PATH or '') if ODA_PATH else '✗ (DXF만)'}")
    print(f"  http://localhost:{port}")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)