"""Generate synthetic broadsheet front pages for testing the OCR splash extractor (dev only).

Each front has a splash (headline, deck, byline, body in columns ending "Continued on page N"),
a photo with caption, competing stories, a skybox and an advert. Ground truth is written next to
each image so tests can score recall (splash words found) and precision (no other-story words).

    python tools/make_synthetic_fronts.py        # writes tests/fixtures/fronts/*.jpg + *.json
"""
import io, json, pathlib
from PIL import Image, ImageFilter
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONTS = ROOT / "app/static/fonts"
OUT = ROOT / "tests/fixtures/fronts"; OUT.mkdir(parents=True, exist_ok=True)

SPLASH_FT = dict(
    headline="Riots reignite French debt rout as Macron struggles to restore order",
    deck="Gap with German borrowing costs widens to 150 basis points as curfews imposed in Paris, Lille and Nice",
    byline="LEILA ABBOUD — PARIS",
    paras=[
        "Investors dumped French government bonds yesterday as violent protests spread across the country, pushing the gap between French and German borrowing costs to its widest level since the eurozone debt crisis.",
        "The spread between ten-year French and German yields rose to about 150 basis points, according to traders, as the government imposed overnight curfews in Paris, Lille and Nice after a weekend of rioting and looting.",
        "Analysts said the unrest had made it harder for the prime minister to pass a budget that would reassure markets about the trajectory of public debt, which is forecast to keep rising next year.",
        "Officials insisted that public finances remained under control and that spending plans would be presented to parliament as scheduled, despite calls from opposition parties to delay the vote.",
        "Bank shares also fell sharply, with the largest lenders losing between four and six per cent, while the euro weakened against the dollar and sterling in afternoon trading in London.",
        "Fund managers said they had cut their holdings of French debt in recent weeks, warning that a failure to pass the budget could prompt rating agencies to downgrade the country again.",
        "The finance ministry said it would not change its deficit target for next year, and that the measures already announced would be enough to stabilise the ratio of debt to national output.",
    ],
    page=4)
OTHER_FT = [
    dict(headline="Chancellor rules out capital gains tax rise",
         paras=["The chancellor has ruled out raising capital gains tax in next month's Budget, easing concerns among entrepreneurs and investors who had warned that a sharp increase would drive capital overseas.",
                "Treasury officials are instead examining changes to pension tax relief and property levies to close a shortfall in the public finances."]),
    dict(headline="Thames Water creditors face losses under new plan",
         paras=["Creditors of Thames Water could be forced to accept heavy losses under options being drawn up by officials, after the prime minister ordered a rethink of plans for the struggling utility."]),
]
SPLASH_TIMES = dict(
    headline="Labour cuts £2bn from Army front line",
    deck="Armoured vehicle variants scrapped as defence chiefs warn of gaps in capability",
    byline="By Larisa Brown, Defence Editor",
    paras=[
        "Mortar, repair and overwatch versions of the Boxer armoured vehicle have been dropped, leaving the army to find other ways to fill gaps in its front-line capability.",
        "Ministers confirmed in answers to parliament that almost two billion pounds had been removed from the programme, which was meant to form the backbone of the mechanised infantry.",
        "Senior officers said the decision would leave units without specialist vehicles for several years, while the Ministry of Defence said the requirement would be met in other ways.",
        "The opposition said the cuts showed the government was not serious about defence, weeks after promising to raise spending as a share of national income.",
        "The vehicle was ordered as a family of variants that would share parts and training. Dropping three of them means older vehicles must stay in service for longer than planned.",
        "Defence sources said the savings would be spent on drones, air defence and long-range missiles, which ministers regard as the main lesson of the war in Ukraine.",
        "A former head of the army said the service was being asked to do more with less, and warned that it could no longer send a full division abroad at short notice.",
    ],
    page=6)
OTHER_TIMES = [
    dict(headline="Curfews across France after nights of rioting",
         paras=["Curfews were imposed in several French cities last night after a weekend of rioting in which more than two thousand people were arrested and shops were looted."]),
    dict(headline="Hospitals not ready for winter, leaders warn",
         paras=["Health service leaders have warned that hospitals are already struggling with demand and are not prepared for another winter crisis."]),
]

def font_css():
    f = lambda n: (FONTS / n).as_uri()
    return f"""@font-face{{font-family:Body;src:url('{f("SourceSerif4-Variable.ttf")}')}}
@font-face{{font-family:Body;font-style:italic;src:url('{f("SourceSerif4-Italic-Variable.ttf")}')}}
@font-face{{font-family:Head;src:url('{f("PlayfairDisplay-Variable.ttf")}')}}
@font-face{{font-family:Sans;src:url('{f("LibreFranklin-Variable.ttf")}')}}"""

def story(s, cls="", cols=1, head_mm=9, cont=None):
    body = "".join(f"<p>{p}</p>" for p in s["paras"])
    if cont: body += f'<p class="cont">Continued on page {cont}</p>'
    deck = f'<div class="deck">{s["deck"]}</div>' if s.get("deck") else ""
    by = f'<div class="by">{s["byline"]}</div>' if s.get("byline") else ""
    return (f'<div class="story {cls}"><h2 style="font-size:{head_mm}mm">{s["headline"]}</h2>{deck}{by}'
            f'<div class="body" style="column-count:{cols}">{body}</div></div>')

PHOTO = '<div class="photo"><div class="img"></div><div class="cap">Protesters clash with police in central Paris on Sunday night. Photograph: Agency</div></div>'

def page(kind):
    if kind == "ft":
        name, sp, others, bg = "FINANCIAL TIMES", SPLASH_FT, OTHER_FT, "#fff1e5"
        grid = f"""
<div class="row" style="height:255mm">
  <div style="width:236mm">{story(sp, "splash", cols=4, head_mm=15, cont=sp["page"])}</div>
  <div class="rule"></div>
  <div style="width:84mm">{PHOTO}{story(others[0], cols=1, head_mm=7)}</div>
</div>
<div class="hr"></div>
<div class="row" style="height:110mm">
  <div style="width:150mm">{story(others[1], cols=2, head_mm=8)}</div><div class="rule"></div>
  <div class="advert" style="width:170mm">ADVERTISEMENT<br><span>Global Private Bank — wealth, planned for generations</span></div>
</div>"""
    else:
        name, sp, others, bg = "THE TIMES", SPLASH_TIMES, OTHER_TIMES, "#ffffff"
        grid = f"""
<div class="splash-wide"><h2 style="font-size:20mm">{sp["headline"]}</h2><div class="deck">{sp["deck"]}</div></div>
<div class="row" style="height:270mm">
  <div style="width:110mm"><div class="by">{sp["byline"]}</div>
     <div class="body" style="column-count:2">{"".join(f"<p>{p}</p>" for p in sp["paras"])}<p class="cont">Continued on page {sp["page"]}</p></div></div>
  <div class="rule"></div>
  <div style="width:210mm">{PHOTO.replace("Paris on Sunday night", "Salisbury Plain last month")}</div>
</div>
<div class="hr"></div>
<div class="row" style="height:95mm">
  <div style="width:160mm">{story(others[0], cols=2, head_mm=8)}</div><div class="rule"></div>
  <div style="width:160mm">{story(others[1], cols=2, head_mm=8)}</div>
</div>"""
    html = f"""<html><head><style>{font_css()}
*{{box-sizing:border-box;margin:0;padding:0}}
.p{{width:380mm;height:578mm;padding:10mm 17mm;background:{bg};font-family:Body;color:#111;overflow:hidden}}
.mast{{text-align:center;font:700 30mm/1 Head;letter-spacing:1mm;border-bottom:1.2mm solid #111;padding-bottom:3mm}}
.date{{display:flex;justify-content:space-between;font:600 4mm/1 Sans;padding:2mm 0;border-bottom:.4mm solid #111;margin-bottom:4mm}}
.sky{{display:flex;gap:4mm;height:22mm;margin-bottom:4mm}}
.sky div{{flex:1;border:.4mm solid #111;font:700 4.2mm/1.2 Sans;padding:2mm;display:flex;align-items:center}}
.sky i{{width:16mm;height:16mm;background:#777;margin-right:3mm;flex:0 0 16mm}}
.row{{display:flex;gap:4mm;overflow:hidden}} .rule{{width:.3mm;background:#111}} .hr{{height:.4mm;background:#111;margin:4mm 0}}
h2{{font-family:Head;font-weight:800;line-height:1.02;margin-bottom:3mm}}
.splash-wide h2{{margin-bottom:3mm}} .splash-wide{{margin-bottom:4mm}}
.deck{{font:italic 400 6.4mm/1.2 Head;margin-bottom:3mm}}
.by{{font:700 3.4mm/1 Sans;letter-spacing:.3mm;margin-bottom:3mm}}
.body{{column-gap:4mm;font-size:8.5pt;line-height:10.2pt;text-align:justify;hyphens:auto}}
.body p{{text-indent:3mm}} .body p:first-child{{text-indent:0}}
.cont{{font:700 7.5pt/10pt Sans;text-indent:0!important;margin-top:1mm}}
.photo .img{{height:120mm;background:linear-gradient(135deg,#333,#999 40%,#555 60%,#bbb)}}
.photo .cap{{font:italic 7pt/8.5pt Body;margin:2mm 0 4mm}}
.advert{{border:.6mm solid #111;display:flex;flex-direction:column;align-items:center;justify-content:center;
  font:700 5mm/1.4 Sans;text-align:center}} .advert span{{font:400 9mm/1.1 Head}}
</style></head><body><div class="p">
<div class="mast">{name}</div>
<div class="date"><span>WEDNESDAY 21 OCTOBER 2026</span><span>UK £3.50</span></div>
<div class="sky"><div><i></i>Lucy Kellaway: the office party is back</div><div><i></i>Wine: the best bottles under £15</div><div><i></i>Markets: what the bond sell-off means</div></div>
{grid}
</div></body></html>"""
    return html, sp, others

def render(kind, widths=(1400, 1200, 1000), seed=0):
    html, sp, others = page(kind)
    tmp = OUT / f"_{kind}.html"; tmp.write_text(html)
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 2200}, device_scale_factor=2.8)
        pg.goto(tmp.as_uri()); pg.wait_for_timeout(400)
        master = Image.open(io.BytesIO(pg.locator(".p").screenshot())).convert("RGB"); b.close()
    tmp.unlink()
    truth = {"headline": sp["headline"], "deck": sp["deck"], "body": " ".join(sp["paras"]), "page": sp["page"],
             "other_text": " ".join(o["headline"] + " " + " ".join(o["paras"]) for o in others)}
    for w in widths:
        im = master.resize((w, round(master.height * w / master.width)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.4))
        name = f"{kind}-{w}"
        im.save(OUT / f"{name}.jpg", quality=72)
        (OUT / f"{name}.json").write_text(json.dumps(truth, indent=1))
        print("wrote", name, im.size)

if __name__ == "__main__":
    render("ft"); render("times")
