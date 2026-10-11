"""Ikon El Niño Outlook report page.

Run after ikon_snow.py and ikon_validate.py: python ikon_report.py  -> output/ikon/report.html, which loads the
resort PNGs sitting next to it. Reads output/ikon/forecast.csv and data/validation/summary.csv.
"""
import html

import pandas as pd

import ikon_snow as k

d = pd.read_csv(k.OUT / "forecast.csv")
val = pd.read_csv("data/validation/summary.csv")
real = pd.read_csv("data/validation/enso_check.csv").set_index("resort")  # best long measured record per resort
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


def real_cell(name):
    if name not in real.index:
        return '<td class="num muted">–</td>'
    v = real.loc[name]
    return (f'<td class="num"><b>{v.p_above_obs * 100:.0f}%</b>'
            f'<span class="sub">{html.escape(record(name, v.series))}, {v.n} yrs</span></td>')


def table(g, south=False):
    rows = []
    for r in g.sort_values("p_above", ascending=False).itertuples():
        rec = f"{r.strong_above}/{r.strong_n}"
        extra = f'<td class="num {"pos" if r.to_date >= 0 else "neg"}">{r.to_date:+.0f}%</td>' if south else ""
        rows.append(
            f'<tr><th scope="row"><a href="#{r.png[:-4]}">{html.escape(r.name)}</a>'
            f'<span class="sub">{html.escape(r.country)} · normal {r.normal_in:.0f}&Prime; ({r.normal_in * 2.54:.0f} cm)</span></th>'
            f'<td>{access(r.access)}</td>'
            f'<td class="num"><b>{r.p_above * 100:.0f}%</b>{meter(r.p_above)}</td>{real_cell(r.name)}'
            f'<td class="num">{r.pred:+.0f}%</td><td class="vis">{bar(r)}</td>'
            f'<td class="num muted">{r.r:+.2f}</td><td class="num muted">{rec}</td>{extra}</tr>')
    head = ('<tr><th scope="col">Resort</th><th scope="col">Base pass</th><th scope="col">Chance above normal</th>'
            '<th scope="col" title="Same line fit on the best long measured snowfall record near the resort">Real record</th>'
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


AGENCY = {"Segl Maria": "MeteoSwiss", "Zermatt": "MeteoSwiss", "Galtur": "GeoSphere Austria", "Jochberg": "eHYD Tirol",
          "Hahnenkamm Ehrenbachhohe": "GeoSphere Austria", "Chamonix": "Météo-France", "Megeve": "Météo-France",
          "Hospitalet": "Météo-France", "Porte Puymorens": "Météo-France"}
RESORT_PAGE = {"Mammoth Mountain", "Palisades Tahoe", "Big Bear Mountain Resort", "Sierra-at-Tahoe"}


def record(resort, series):
    """Human label for a validation series: 'NOAA Rainier Paradise', 'Resort report', 'OnTheSnow'."""
    if series.startswith("station: "):
        name = series.removeprefix("station: ")
        return f"{AGENCY.get(name, 'NOAA')} {name}"
    return "Resort report" if resort in RESORT_PAGE else "OnTheSnow"


def validation():
    e = real.reset_index().merge(d[["name", "region", "p_above"]], left_on="resort", right_on="name")
    e = e.sort_values(["region", "p_above_obs"], ascending=[False, False])
    enso_rows = "".join(
        f'<tr><th scope="row">{html.escape(v.resort)}<span class="sub">{html.escape(record(v.resort, v.series))}</span></th>'
        f'<td class="num">{v.n}</td><td class="num">{v.r:.2f}</td>'
        f'<td class="num {"pos" if v.oni_r_obs >= 0 else "neg"}"><b>{v.oni_r_obs:+.2f}</b></td>'
        f'<td class="num muted">{v.oni_r_model:+.2f}</td>'
        f'<td class="num"><b>{v.p_above_obs * 100:.0f}%</b></td><td class="num muted">{v.p_above * 100:.0f}%</td></tr>'
        for v in e.itertuples())
    rows = "".join(
        f'<tr><th scope="row">{html.escape(v.resort)}<span class="sub">{html.escape(record(v.resort, v.series))}</span></th>'
        f'<td class="num">{v.n}</td><td class="num">{v.seasons.replace("..", " to ")}</td>'
        f'<td class="num"><b>{v.r:.2f}</b></td><td class="num">{v.same_side * 100:.0f}%</td>'
        f'<td class="num muted">{v.ratio:.1f}×</td></tr>'
        for v in val.itertuples())
    link = e.oni_r_obs.corr(e.oni_r_model)
    return f"""<section id="validation"><h2>Validation against real snowfall</h2>
  <p>The outlook rests on modelled snowfall, so it was checked against {len(val)} observed records at {val.resort.nunique()} resorts: resort snowfall histories, OnTheSnow totals, and long new-snow records from NOAA, MeteoSwiss, GeoSphere Austria, the Tirol hydrographic service and Météo-France. Two questions matter. Does the model get the good and bad seasons right? And does the real snow show the same El Niño tilt?</p>
  <h3>El Niño link in long measured records</h3>
  <p class="note">The best record of 30 or more seasons at each resort, refitted exactly like the outlook. Departures are from each record's own average, so the odds mean above that record's average. Across these {len(e)} records the model's ENSO links line up with the real ones at r = {link:.2f}, with clear exceptions.</p>
  <div class="tablewrap"><table><thead><tr><th scope="col">Resort and record</th><th scope="col">Seasons</th>
    <th scope="col" title="Correlation of observed and modelled season totals">Tracks model</th>
    <th scope="col" title="Correlation of observed season snowfall with peak ONI">ENSO link, real</th><th scope="col">ENSO link, model</th>
    <th scope="col">Odds, real record</th><th scope="col">Odds, model</th></tr></thead><tbody>{enso_rows}</tbody></table></div>
  <div class="cols">
    <div><h3>Confirmed</h3><p class="note">Southern California leans snowy (Big Bear Lake, 41 seasons: +0.25, about 70%). The Pacific Northwest and northern Idaho lean dry, Paradise on Mount Rainier most of all: −0.40 over 47 seasons, the strongest real signal found, which puts Crystal near a one-in-five chance of an above-average winter.</p></div>
    <div><h3>Not confirmed</h3><p class="note">The model tilts the western Alps snowy, but none of the seven long Alpine records agrees. Chamonix, Megève and Zermatt sit at −0.06 to +0.03 over 56–64 seasons, and Segl-Maria (St. Moritz) and Galtür (Ischgl) lean slightly dry. The Alps are a coin flip in El Niño winters. Northern Vermont and western Maine lean dry (Mount Mansfield, Rangeley), which the model only partly catches.</p></div>
  </div>
  <details><summary>All {len(val)} records: how closely the model tracks each season</summary>
  <div class="tablewrap"><table><thead><tr><th scope="col">Resort and record</th><th scope="col">Seasons</th><th scope="col">Span</th>
    <th scope="col" title="Correlation of observed and modelled season totals">r</th><th scope="col" title="Seasons where both are on the same side of their average">Same side</th>
    <th scope="col" title="Observed average divided by modelled average">Obs ÷ model</th></tr></thead><tbody>{rows}</tbody></table></div></details>
  <p class="note">Median season-to-season r is about 0.65 for both station and resort records, highest in California (0.82–0.99) and lowest at Taos, Grandvalira and Valle d'Aosta. Resort reports run up to 2.8 times the model; Alpine valley stations run below it. Stations sit up to 50 km from the resort and often well below the slopes. Seasons missing more than 10 winter days are dropped. No open new-snow data exists for Valle d'Aosta or the Dolomites. OnTheSnow series start in 2012-13, too short to test El Niño.</p>
</section>"""


reg = {k: g for k, g in d.groupby("region")}
na, eu, asia, sh = reg["North America"], reg["Europe"], reg["Asia"], reg["Southern Hemisphere"]


def pick(name):
    return d[d.name == name].iloc[0]


def card(name, note):
    r = pick(name)
    base = "unlimited" if r.access == "unlimited" else "5 days"
    if name in real.index:
        v = real.loc[name]
        odds, sub = v.p_above_obs, f"real record ({html.escape(record(name, v.series))}) · model {r.p_above * 100:.0f}% · {base}"
    else:
        odds, sub = r.p_above, f"model · predicted {r.pred:+.0f}% · {base} on Base"
    return (f'<li><a href="#{r.png[:-4]}">{html.escape(name)}</a><span class="big">{odds * 100:.0f}%</span>'
            f'<span class="sub">{sub}</span><p>{note}</p></li>')


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
  <p>A very strong El Niño shifts the winter storm track south. In both the reanalysis and two dozen long measured snow records, that has meant more snow for Southern California and less for the Pacific Northwest, northern Idaho, interior British Columbia and the Canadian Rockies. The model also tilts the western Alps snowy, but 46 to 76 seasons of Swiss, French and Austrian station records show no such effect, so treat the Alps as a coin flip. Even where the link is strongest, El Niño explains under a fifth of the winter-to-winter swing.</p>
  <h3>Best odds</h3>
  <ul class="picks">
    {card("Big Bear Mountain Resort", "The most reliable El Niño link on the pass, confirmed by 41 seasons at Big Bear Lake. The model says 80%; the real record says about 70%. Unlimited on Base.")}
    {card("Snow Valley", "Same storms as Big Bear, 15 km west, and its OnTheSnow totals track the model closely (r = 0.82). No long record to check the El Niño link, so expect it to run lower like Big Bear, nearer 70%.")}
    {card("Mammoth Mountain", "The model is lukewarm, but Mammoth's own 57-year record beat its average in 6 of 7 strong El Niño winters. Strong events help here; weak ones do little. Unlimited on Base.")}
    {card("Taos Ski Valley", "Leans snowy in both the model and the nearest long record, but the model tracks Taos snowfall poorly, so this is the least certain of the four.")}
  </ul>
  <h3>Europe: no reliable tilt</h3>
  <p class="note">Real-record odds of an above-average winter: Megève {real.loc["Megeve Ski Area"].p_above_obs * 100:.0f}%, Zermatt {real.loc["Zermatt Matterhorn"].p_above_obs * 100:.0f}%, Kitzbühel {real.loc["Kitzbuhel"].p_above_obs * 100:.0f}%, Chamonix {real.loc["Chamonix Mont-Blanc Valley"].p_above_obs * 100:.0f}%, Ischgl {real.loc["Ischgl"].p_above_obs * 100:.0f}%, St. Moritz {real.loc["St. Moritz"].p_above_obs * 100:.0f}%. None of these links is statistically meaningful, so pick an Alpine trip on other grounds. If anything, the eastern Alps (St. Moritz, Ischgl) lean slightly dry.</p>
  <h3>Weakest odds</h3>
  <ul class="picks bad">
    {card("Crystal Mountain", "Paradise on Mount Rainier has the strongest real El Niño signal found (−0.40 over 47 seasons), stronger than the model. The model gives Snoqualmie and Mt. Bachelor the same dry tilt; their own records are too short to confirm it.")}
    {card("Schweitzer", "Sandpoint's 58-season record agrees with the model: northern Idaho runs dry in El Niño winters.")}
    {card("Sugarloaf", "Rangeley (51 seasons) and Mount Mansfield near Sugarbush (50 seasons) both lean dry, a northern New England tilt the model mostly misses.")}
    {card("The Highlands", "Michigan and Minnesota resorts (The Highlands, Boyne, Lutsen) won only 2–4 of 9 strong El Niño winters in the model. No long station check yet.")}
  </ul>
  <div class="cols">
    <div><h3>Tahoe and the Northeast</h3><p class="note">Palisades and Sierra-at-Tahoe beat normal in 7 of 9 strong El Niño winters in the model, but Tahoe City's 73-year record is close to neutral (54%): strong events help and weak ones don't. Stratton, Loon and Sunday River sit near even odds in both the model and the station records; Killington leans slightly dry (41% at Chittenden).</p></div>
    <div><h3>Southern Hemisphere, 2026 season</h3><p class="note">The El Niño season is already here and nearly over. Valle Nevado has the strongest link of any resort (r = +0.52) and is at {pick("Valle Nevado").to_date:+.0f}% so far. Thredbo ({pick("Thredbo").to_date:+.0f}%) and Mt Buller ({pick("Mt Buller").to_date:+.0f}%) are having the poor season Australian history predicts. The New Zealand resorts are running 26–30% below normal despite a slightly positive history.</p></div>
  </div>
</section>

<section id="how-to-read">
  <h3>How to read the tables</h3>
  <p class="note"><b>Chance above normal</b> is the probability this winter beats the resort's 1991–2020 average, from a line fitted through every winter since 1950–51 against its peak ONI and evaluated at +{ONI:.2f}. <b>Predicted</b> is that line's departure; the range bar shows ±1 standard deviation of past winters around the line (about two winters in three fall inside it; axis −60% to +110%). <b>r</b> measures how tightly modelled snowfall follows ENSO. <b>Real record</b> is the same fit run on the best long measured snowfall record near the resort (30+ seasons), where one exists; trust it over the model where they disagree. <b>Strong El Niño record</b> counts strong and very strong winters (peak ONI ≥ 1.5) that beat normal. Select a resort name for its season-by-season chart.</p>
</section>

<section id="north-america"><h2>North America</h2>{table(na)}</section>
<section id="europe"><h2>Europe</h2><p class="note">The model tilts the western Alps snowy, but the long station records in the Real record column do not. Read every European row as close to even odds.</p>{table(eu)}</section>
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
    <li><b>Real-record odds</b> repeat the fit on measured station or resort records (see Validation). They use each record's own average rather than the 1991–2020 normal and cover fewer seasons, so they are noisier, but they are free of the model's blind spots.</li>
    <li><b>Samples are small.</b> There have been 9 strong or very strong El Niño winters since 1950. The fitted line uses all 76 complete winters, but an r below about 0.23 is within the range chance alone produces.</li>
    <li><b>Resort list and coordinates</b> come from ikonpass.com (2026–27 Base Pass) and public sources. Multi-mountain areas (Dolomiti Superski, Valle d'Aosta, Niseko United) use one representative point.</li>
  </ul>
  <p class="note">Built with <code>ikon_snow.py</code> in the data-viz repo. Data: Copernicus Climate Change Service ERA5; NOAA Climate Prediction Center ONI; NOAA COOP via RCC-ACIS; MeteoSwiss; GeoSphere Austria; eHYD; Météo-France; resort reports and OnTheSnow.</p>
</section>
</main>
"""
(k.OUT / "report.html").write_text(page, encoding="utf-8")
