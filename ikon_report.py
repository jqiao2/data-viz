"""Ikon El Niño Outlook report page.

Run after ikon_snow.py and ikon_validate.py: python ikon_report.py  -> output/ikon/report.html, which loads the
resort PNGs sitting next to it. Reads output/ikon/forecast.csv and data/validation/summary.csv.
"""
import html

import pandas as pd

import ikon_snow as k

d = pd.read_csv(k.OUT / "forecast.csv")
val = pd.read_csv("data/validation/summary.csv")
ONI = k.latest_oni(k.oni())
LO, HI = -60, 110  # departure axis for the row bars (%)
W, H = 180, 18


def x(v):
    return (min(max(v, LO), HI) - LO) / (HI - LO) * W


def bar(r):
    side = "above" if r.pred >= 0 else "below"
    return (f'<svg class="dep" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="Predicted {r.pred:+.0f}%, likely range {r.pred - r.sd:+.0f}% to {r.pred + r.sd:+.0f}%">'
            f'<line class="zero" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="1" y2="{H - 1}"/>'
            f'<rect class="band {side}" x="{x(r.pred - r.sd):.1f}" y="6" width="{x(r.pred + r.sd) - x(r.pred - r.sd):.1f}" '
            f'height="6" rx="3"/><circle class="dot {side}" cx="{x(r.pred):.1f}" cy="9" r="4.5"/></svg>')


def meter(p):
    return (f'<span class="meter"><span class="fill {"above" if p >= 0.5 else "below"}" '
            f'style="width:{p * 100:.0f}%"></span><span class="half"></span></span>')


def access(a):
    return '<span class="pill unl">Unlimited</span>' if a == "unlimited" else '<span class="pill lim">5 days</span>'


def table(g, south=False):
    rows = []
    for r in g.sort_values("p_above", ascending=False).itertuples():
        rec = f"{r.strong_above}/{r.strong_n}"
        extra = f'<td class="num {"pos" if r.to_date >= 0 else "neg"}">{r.to_date:+.0f}%</td>' if south else ""
        rows.append(
            f'<tr><th scope="row"><a href="#{r.png[:-4]}">{html.escape(r.name)}</a>'
            f'<span class="sub">{html.escape(r.country)} · normal {r.normal_in:.0f}&Prime; ({r.normal_in * 2.54:.0f} cm)</span></th>'
            f'<td>{access(r.access)}</td>'
            f'<td class="num"><b>{r.p_above * 100:.0f}%</b>{meter(r.p_above)}</td>'
            f'<td class="num">{r.pred:+.0f}%</td><td class="vis">{bar(r)}</td>'
            f'<td class="num muted">{r.r:+.2f}</td><td class="num muted">{rec}</td>{extra}</tr>')
    head = ('<tr><th scope="col">Resort</th><th scope="col">Base pass</th><th scope="col">Chance above normal</th>'
            '<th scope="col">Predicted</th><th scope="col" class="vis">Range (±1 SD)</th>'
            '<th scope="col" title="Correlation of season snowfall with peak ONI, 1950–2025">r</th>'
            '<th scope="col" title="Strong or very strong El Niño seasons above normal">Strong El Niño record</th>'
            + ('<th scope="col">2026 to date</th>' if south else "") + '</tr>')
    return f'<div class="tablewrap"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'


def gallery(g):
    items = "".join(
        f'<details id="{r.png[:-4]}"><summary>{html.escape(r.name)} <span class="sub">{r.p_above * 100:.0f}% · '
        f'{r.pred:+.0f}%</span></summary><img loading="lazy" src="{r.png}" '
        f'alt="Snowfall departure in each El Niño season at {html.escape(r.name)}, grouped by strength"></details>'
        for r in g.sort_values("name").itertuples())
    return f'<div class="gallery">{items}</div>'


SOURCES = {('Palisades Tahoe', 'resort report'): 'Resort, 8,000 ft snow plot', ('Mammoth Mountain', 'resort report'): 'Resort, Main Lodge', ('Snow Valley', 'resort report'): 'OnTheSnow', ('Palisades Tahoe', 'NOAA station'): 'NOAA Tahoe City, 6,230 ft', ('Big Bear Mountain Resort', 'resort report'): 'Resort (Snow Summit / Bear Mtn)', ('Big Bear Mountain Resort', 'NOAA station'): 'NOAA Big Bear Lake, 6,750 ft', ('Megeve Ski Area', 'resort report'): 'OnTheSnow', ('Zermatt Matterhorn', 'resort report'): 'OnTheSnow', ('Chamonix Mont-Blanc Valley', 'resort report'): 'OnTheSnow', ('Taos Ski Valley', 'resort report'): 'OnTheSnow', ('Taos Ski Valley', 'NOAA station'): 'NOAA Eagle Nest, 8,280 ft, 20 km away'}


def validation():
    rows = "".join(
        f'<tr><th scope="row">{html.escape(v.resort)}<span class="sub">{html.escape(SOURCES.get((v.resort, v.series), v.series))}</span></th>'
        f'<td class="num">{v.n}</td><td class="num">{v.seasons.replace("..", " to ")}</td>'
        f'<td class="num"><b>{v.r:.2f}</b></td><td class="num">{v.same_side * 100:.0f}%</td>'
        f'<td class="num muted">{v.ratio:.1f}×</td>'
        f'<td class="num">{v.strong_obs_above}/{v.strong_n} · {v.strong_model_above}/{v.strong_n}</td>'
        f'<td class="num muted">{v.oni_r_obs:+.2f} · {v.oni_r_model:+.2f}</td></tr>'
        for v in val.itertuples())
    return f"""<section id="validation"><h2>Validation against real snowfall</h2>
  <p>The model's totals were never meant to match resort reports, and they don't: Mammoth and Big Bear report about 2.5 times the modelled snowfall, while the Alps resorts report less. What matters is whether the model gets the good and bad seasons right. Each series below is compared season by season with the model, both measured from their own average over the seasons they share.</p>
  <div class="tablewrap"><table><thead><tr><th scope="col">Resort and record</th><th scope="col">Seasons</th><th scope="col">Span</th>
    <th scope="col" title="Correlation of observed and modelled season totals">r</th><th scope="col" title="Seasons where both are on the same side of their average">Same side</th>
    <th scope="col" title="Observed average divided by modelled average">Obs ÷ model</th>
    <th scope="col" title="Strong El Niño seasons above average: observed · model">Strong El Niño above, obs · model</th>
    <th scope="col" title="Correlation with peak ONI: observed · model">ENSO link, obs · model</th></tr></thead><tbody>{rows}</tbody></table></div>
  <div class="cols">
    <div><h3>California holds up</h3><p class="note">Palisades, Mammoth, Snow Valley and Big Bear track at r = 0.72–0.95, so the El Niño tilt there comes from real weather. Two refinements: Big Bear's NOAA station shows a weaker ENSO link than the model (+0.25 vs +0.35), and Mammoth's own record beats average in more strong El Niño winters than the model does (6 of 7 vs 4 of 7).</p></div>
    <div><h3>Taos and the Alps are uncertain</h3><p class="note">Taos tracks at only r = 0.32–0.33 against both its OnTheSnow totals and the nearest long NOAA record. The Alps series cover only 12–13 seasons of self-reported OnTheSnow totals with two strong El Niño winters, so they neither confirm nor rule out the European tilt.</p></div>
  </div>
  <p class="note">Sources: resort snowfall-history pages, OnTheSnow historical snowfall, and NOAA COOP stations via the RCC-ACIS API (seasons missing more than 10 winter days are dropped). Snow Valley uses OnTheSnow throughout; the resort's own page runs about 25% higher from 2022-23. Palisades 2025-26 is dropped because the table shows no March snowfall.</p>
</section>"""


reg = {k: g for k, g in d.groupby("region")}
na, eu, asia, sh = reg["North America"], reg["Europe"], reg["Asia"], reg["Southern Hemisphere"]


def pick(name):
    return d[d.name == name].iloc[0]


def card(name, note):
    r = pick(name)
    return (f'<li><a href="#{r.png[:-4]}">{html.escape(name)}</a><span class="big">{r.p_above * 100:.0f}%</span>'
            f'<span class="sub">predicted {r.pred:+.0f}% · {"unlimited" if r.access == "unlimited" else "5 days"} on Base</span>'
            f'<p>{note}</p></li>')


page = f"""<title>Ikon El Niño Outlook</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75,700;75,800&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>
:root {{
  --ground:#f3f6f8; --surface:#ffffff; --ink:#15212b; --muted:#5a6773; --rule:#dce3e9;
  --above:#2a78c4; --above-soft:#c9dcef; --below:#c8554f; --below-soft:#f0d3d0; --unl:#1f6b4f; --unl-soft:#d9eee5;
  --display:"Archivo","Arial Narrow",system-ui,sans-serif; --body:"Source Sans 3",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme:dark; --ground:#0e1418; --surface:#151d23; --ink:#e4ebf0; --muted:#94a2ae; --rule:#26323b;
  --above:#5ea5e8; --above-soft:#1f3a55; --below:#e47d76; --below-soft:#4a2624; --unl:#6fcfa6; --unl-soft:#173a2e; }} }}
:root[data-theme="dark"] {{ color-scheme:dark; --ground:#0e1418; --surface:#151d23; --ink:#e4ebf0; --muted:#94a2ae;
  --rule:#26323b; --above:#5ea5e8; --above-soft:#1f3a55; --below:#e47d76; --below-soft:#4a2624; --unl:#6fcfa6; --unl-soft:#173a2e; }}
body {{ background:var(--ground); color:var(--ink); font:16px/1.55 var(--body); padding-inline:16px; padding-block:40px 64px; }}
main {{ max-width:1080px; margin:0 auto; display:grid; gap:48px; }}
h1,h2,h3 {{ font-family:var(--display); font-stretch:75%; text-wrap:balance; line-height:1.05; margin:0; }}
h1 {{ font-size:clamp(40px,7vw,72px); font-weight:800; letter-spacing:-0.01em; }}
h2 {{ font-size:32px; font-weight:800; }}
h3 {{ font-size:15px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }}
p {{ margin:0; max-width:68ch; }}
a {{ color:inherit; text-decoration-color:var(--above); text-underline-offset:3px; }}
a:focus-visible, summary:focus-visible {{ outline:2px solid var(--above); outline-offset:2px; }}
.sub {{ display:block; font-size:13px; color:var(--muted); font-weight:400; }}
header {{ display:grid; gap:16px; }}
.eyebrow {{ font:600 13px/1 var(--mono); letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }}
.dek {{ font-size:19px; color:var(--muted); }}
.oni {{ display:flex; flex-wrap:wrap; gap:8px 24px; align-items:baseline; border-block:1px solid var(--rule); padding-block:14px; }}
.oni b {{ font:600 28px/1 var(--mono); }} .oni span {{ color:var(--muted); font-size:14px; }}
nav {{ display:flex; flex-wrap:wrap; gap:8px; }}
nav a {{ font-size:14px; text-decoration:none; border:1px solid var(--rule); border-radius:999px; padding:4px 12px; background:var(--surface); }}
section {{ display:grid; gap:20px; }}
.bottom {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px; padding:24px; display:grid; gap:20px; }}
.picks {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:16px; list-style:none; margin:0; padding:0; }}
.picks li {{ display:grid; gap:4px; align-content:start; }}
.picks a {{ font-weight:600; }}
.picks .big {{ font:600 34px/1.1 var(--mono); color:var(--above); }}
.picks.bad .big {{ color:var(--below); }}
.picks p {{ font-size:14px; color:var(--muted); }}
.cols {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:24px 40px; }}
.cols ul {{ margin:0; padding-left:18px; display:grid; gap:6px; }}
.tablewrap {{ overflow-x:auto; background:var(--surface); border:1px solid var(--rule); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; min-width:820px; font-size:14px; }}
th, td {{ padding:8px 12px; border-bottom:1px solid var(--rule); text-align:left; vertical-align:middle; }}
thead th {{ font:600 11.5px/1.2 var(--body); text-transform:uppercase; letter-spacing:.06em; color:var(--muted); position:sticky; top:0; background:var(--surface); }}
tbody tr:last-child > * {{ border-bottom:0; }}
tbody th {{ font-weight:600; }}
tbody tr:hover {{ background:var(--ground); }}
.num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
.num b {{ display:inline-block; min-width:3.2ch; }}
.muted {{ color:var(--muted); }} .pos {{ color:var(--above); }} .neg {{ color:var(--below); }}
.meter {{ display:inline-block; position:relative; width:56px; height:6px; border-radius:3px; background:var(--rule); margin-left:8px; vertical-align:middle; overflow:hidden; }}
.meter .fill {{ position:absolute; inset:0 auto 0 0; border-radius:3px; }}
.meter .fill.above {{ background:var(--above); }} .meter .fill.below {{ background:var(--below); }}
.meter .half {{ position:absolute; left:50%; top:0; bottom:0; width:2px; background:var(--surface); }}
.dep .zero {{ stroke:var(--muted); stroke-width:1; }}
.dep .band.above {{ fill:var(--above-soft); }} .dep .band.below {{ fill:var(--below-soft); }}
.dep .dot.above {{ fill:var(--above); }} .dep .dot.below {{ fill:var(--below); }}
.dep .dot {{ stroke:var(--surface); stroke-width:2; }}
.pill {{ font-size:12px; font-weight:600; border-radius:999px; padding:2px 8px; white-space:nowrap; }}
.pill.unl {{ background:var(--unl-soft); color:var(--unl); }} .pill.lim {{ background:var(--ground); color:var(--muted); border:1px solid var(--rule); }}
.axis {{ font:12px var(--mono); color:var(--muted); }}
.gallery {{ display:grid; gap:6px; }}
details {{ background:var(--surface); border:1px solid var(--rule); border-radius:8px; }}
summary {{ cursor:pointer; padding:10px 14px; font-weight:600; display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }}
summary .sub {{ display:inline; font-family:var(--mono); }}
details img {{ display:block; padding:0 8px 8px; border-radius:6px; }}
.note {{ font-size:14px; color:var(--muted); }}
.method {{ display:grid; gap:12px; font-size:15px; }}
.method ul {{ margin:0; padding-left:18px; display:grid; gap:8px; max-width:78ch; }}
</style>

<main>
<header>
  <span class="eyebrow">Ikon Base Pass 2026–27 · ERA5 1950–2026 · issued 9 Oct 2026</span>
  <h1>Ikon El Niño Outlook</h1>
  <p class="dek">How each of the {len(d)} ski areas on the Ikon Base Pass has fared in El Niño winters since 1950, and what that implies for the very strong event building now.</p>
  <div class="oni"><b>ONI +{ONI:.2f}°C</b><span>NOAA Oceanic Niño Index for Jul–Sep 2026. Very strong is +2.0°C or more.</span></div>
  <nav aria-label="Sections"><a href="#bottom-line">Bottom line</a><a href="#north-america">North America</a><a href="#europe">Europe</a><a href="#asia">Asia</a><a href="#southern">Southern Hemisphere</a><a href="#validation">Validation</a><a href="#charts">Resort charts</a><a href="#method">Method</a></nav>
</header>

<section id="bottom-line" class="bottom">
  <h2>Bottom line</h2>
  <p>A very strong El Niño shifts the winter storm track south. Over 76 winters of reanalysis data that has meant more snow for Southern California, northern New Mexico and the western Alps, and less for the Pacific Northwest, interior British Columbia, the Canadian Rockies, Montana and the Great Lakes. The effect is real but modest: even at the best-placed resort, El Niño explains about 15% of the winter-to-winter swing.</p>
  <h3>Best odds in North America and Europe</h3>
  <ul class="picks">
    {card("Big Bear Mountain Resort", "Strongest and most reliable El Niño link of all 77 resorts. Beat normal in 7 of the 9 strong El Niño winters, with a median of +54%. Real records suggest the model overstates the link a little; nearer 70% is a fair read.")}
    {card("Snow Valley", "Same storms, same story as Big Bear, 15 km west. Unlimited on Base.")}
    {card("Taos Ski Valley", "Smaller gain and the steadiest record, but the least certain pick: the model tracks Taos records poorly (see Validation).")}
    {card("Chamonix Mont-Blanc Valley", "The western Alps lean snowy in El Niño winters. Valle d'Aosta, Megève and Zermatt show the same tilt.")}
  </ul>
  <h3>Weakest odds</h3>
  <ul class="picks bad">
    {card("The Highlands", "Michigan and Minnesota resorts (The Highlands, Boyne, Lutsen) won only 2–4 of 9 strong El Niño winters.")}
    {card("Schweitzer", "Northern Idaho and the Washington Cascades (Snoqualmie, Crystal) lose about 8–11% per degree of ONI.")}
    {card("Sun Peaks Resort", "Interior BC and the SkiBig3 resorts lean dry. Lake Louise and Banff Sunshine beat normal in only 2 of 9.")}
    {card("Grandvalira Resorts", "The only European resort below even odds. The Pyrenees sit south of the storm track the Alps benefit from.")}
  </ul>
  <div class="cols">
    <div><h3>Tahoe and Mammoth</h3><p class="note">The straight-line fit puts them near even odds because weak and moderate El Niños do little there. In strong ones they do better: Palisades and Sierra-at-Tahoe beat normal in 7 of 9, Mammoth in 5 of 9 in the model. Mammoth's own 57-year record does better: above average in 6 of its 7 strong El Niño winters. Treat them as better bets than the table says.</p></div>
    <div><h3>Southern Hemisphere, 2026 season</h3><p class="note">The El Niño season is already here and nearly over. Valle Nevado has the strongest link of any resort (r = +0.52) and is at {pick("Valle Nevado").to_date:+.0f}% so far. Thredbo ({pick("Thredbo").to_date:+.0f}%) and Mt Buller ({pick("Mt Buller").to_date:+.0f}%) are having the poor season Australian history predicts. The New Zealand resorts are running 26–30% below normal despite a slightly positive history.</p></div>
  </div>
</section>

<section id="how-to-read">
  <h3>How to read the tables</h3>
  <p class="note"><b>Chance above normal</b> is the probability this winter beats the resort's 1991–2020 average, from a line fitted through every winter since 1950–51 against its peak ONI and evaluated at +{ONI:.2f}. <b>Predicted</b> is that line's departure; the range bar shows ±1 standard deviation of past winters around the line (about two winters in three fall inside it; axis −60% to +110%). <b>r</b> measures how tightly snowfall follows ENSO. <b>Strong El Niño record</b> counts strong and very strong winters (peak ONI ≥ 1.5) that beat normal. Select a resort name for its season-by-season chart.</p>
</section>

<section id="north-america"><h2>North America</h2>{table(na)}</section>
<section id="europe"><h2>Europe</h2>{table(eu)}</section>
<section id="asia"><h2>Asia</h2><p class="note">Northeast China and Korea lean snowy in El Niño winters, though Yunding's high odds partly reflect a run of snowier recent decades rather than ENSO. Japan's Sea of Japan resorts lean slightly dry.</p>{table(asia)}</section>
<section id="southern"><h2>Southern Hemisphere</h2><p class="note">Seasons run January–December, so the 2026 season is the one that overlaps this El Niño. Its strength comes from the May–September ONI. Only 2 past seasons were strong, so the record column says little.</p>{table(sh, south=True)}</section>

{validation()}

<section id="charts"><h2>Resort charts</h2>
  <p class="note">Every El Niño season since 1950 at each resort, grouped by strength, shown as departure from the 1991–2020 normal. Bars show modelled totals in inches (and cm outside the US); hatched bars are seasons still in progress.</p>
  <h3>North America</h3>{gallery(na)}<h3>Europe</h3>{gallery(eu)}<h3>Asia</h3>{gallery(asia)}<h3>Southern Hemisphere</h3>{gallery(sh)}
  <details><summary>All Northern Hemisphere resorts on one chart</summary><img loading="lazy" src="summary.png" alt="Predicted departure and chance above normal for every Northern Hemisphere resort"></details>
</section>

<section id="method" class="method"><h2>Method and limits</h2>
  <ul>
    <li><b>Snowfall</b> is modelled, not measured. It comes from ERA5 hourly reanalysis (Copernicus) at the grid point nearest each resort, 1950 to the present. ERA5's terrain is smoothed to about 28 km and often sits several hundred to over 1,000 m below the resort, so temperature is adjusted to mid-mountain at 6.5°C per km. Precipitation counts as snow below 0.5°C and rain above 2°C, at 7:1 snow to water. Totals will not match resort reports; departures from each resort's own normal are the meaningful numbers.</li>
    <li><b>El Niño strength</b> is the highest NOAA ONI reading in Nov–Mar for northern resorts and May–Sep for southern ones. The outlook uses the latest reading, +{ONI:.2f} for Jul–Sep. Events usually peak in early winter, so a higher peak would push every tilt slightly further.</li>
    <li><b>Chance above normal</b> assumes winters scatter evenly around the fitted line. It ignores long-term trends, which flatters resorts that have been snowier lately (Yunding) and penalises ones that have dried out.</li>
    <li><b>Samples are small.</b> There have been 9 strong or very strong El Niño winters since 1950. The fitted line uses all 76 complete winters, but an r below about 0.23 is within the range chance alone produces.</li>
    <li><b>Resort list and coordinates</b> come from ikonpass.com (2026–27 Base Pass) and public sources. Multi-mountain areas (Dolomiti Superski, Valle d'Aosta, Niseko United) use one representative point.</li>
  </ul>
  <p class="note">Built with <code>ikon_snow.py</code> in the data-viz repo. Data: Copernicus Climate Change Service ERA5; NOAA Climate Prediction Center ONI.</p>
</section>
</main>
"""
(k.OUT / "report.html").write_text(page, encoding="utf-8")
