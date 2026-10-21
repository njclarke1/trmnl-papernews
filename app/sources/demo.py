"""Demo editions (20–21 Oct 2026 stories, paraphrased) for DEMO_MODE, /demo states and the render harness.

Photos are public-domain/CC0 test images (scikit-image data set), not news pictures.
"""
from __future__ import annotations

import copy
import pathlib

from ..model import Edition, Story

PH = pathlib.Path(__file__).resolve().parents[1] / "demo_photos"
COFFEE, CAT, ASTRO = (f"file://{PH / n}" for n in ("coffee.png", "chelsea.png", "astronaut.png"))

THAMES = ["Andy Burnham has told Whitehall officials to come back with more radical options for Thames Water, in an early test of his pledge to bring essential services under public control.",
          "Ideas under discussion include turning roughly £100m of unpaid environmental fines into a government stake, and placing the company into a special administration regime. Government lawyers regard the second route as difficult while creditors are still funding the business.",
          "Creditors have proposed a £10bn rescue, and the company, which serves 16 million people, has asked them for a further £2.4bn. A water bill being prepared by the environment secretary, Angela Eagle, would lift the cap on government ownership of water companies.",
          "One adviser said any plan that left creditors with nothing would be certain to end up in court. Officials say Thames is a unique case, and that other water companies will be handled through stricter regulation.",
          "Campaigners said the company should go into special administration and then into public ownership, arguing that schemes which kept it in private hands would fail.",
          "A law professor said the best option was to start the administration process before more debt built up, so that the polluter, not the bill payer, would bear the cost.",
          "Thames has been running short of cash for more than a year and has repeatedly asked its lenders for emergency funding while it negotiates a longer-term restructuring.",
          "Ministers are expected to set out their preferred option before Christmas, alongside the water bill that would give them new powers over failing companies."]
FRANCE_FT = ["Investors dumped French government bonds yesterday as violent protests spread across the country, pushing the gap between French and German borrowing costs to its widest level since the eurozone debt crisis.",
             "The spread between ten-year French and German yields rose to about 150 basis points, according to traders, as the government imposed overnight curfews in Paris, Lille and Nice after a weekend of rioting and looting.",
             "Analysts said the unrest had made it harder for the prime minister to pass a budget that would reassure markets about the trajectory of public debt, which is forecast to keep rising next year.",
             "Officials insisted that public finances remained under control and that spending plans would be presented to parliament as scheduled, despite calls from opposition parties to delay the vote.",
             "Bank shares also fell sharply, with the largest lenders losing between four and six per cent, while the euro weakened against the dollar and sterling in afternoon trading in London."]


def S(h, sec="", sf="", **kw):
    return Story(headline=h, section=sec, standfirst=sf, **kw)


EDITIONS: dict[str, Edition] = {
    "guardian": Edition("guardian", "web_front", Story(
        "Burnham orders radical rethink of options to fix Thames Water",
        "Prime minister tells officials to look beyond a creditor rescue, with special administration and a government stake on the table",
        THAMES, "Political staff", "Thames Water", "https://www.theguardian.com/", COFFEE,
        "Test photograph. The live feed supplies the story’s own picture.", is_free=True),
        [S("US resumes sharing intelligence on Russian strikes with Ukraine, Zelenskyy says", "World"),
         S("France imposes overnight curfews in Paris, Lille and Nice after weekend of rioting", "Europe"),
         S("Guinea-Bissau police seize vaccines at centre of newborn research row", "Global health")],
        note="Guardian Content API · editors’ picks"),
    "bbc": Edition("bbc", "web_front", Story(
        "France imposes curfews after weekend of riots and looting",
        "About 2,400 people were arrested as cost-of-living protests turned violent, and the interior minister admits police lost control in some cities",
        [], "BBC News", "Europe", "https://www.bbc.co.uk/news", COFFEE, "Test photograph"),
        [S("Thames Water: officials told to draw up radical options", "UK", "Special administration and a government stake are among the ideas being examined."),
         S("NHS ‘not ready’ for another winter crisis, leaders warn", "Health", "Hospitals say they are already struggling to keep up with demand."),
         S("Childcare cap for larger families set to be lifted", "Family", "The change would follow the end of the two-child limit on universal credit."),
         S("US resumes sharing intelligence with Kyiv", "Ukraine", "Zelenskyy says warnings about incoming Russian strikes have restarted.")],
        note="bbc.co.uk/news · homepage order"),
    "ft": Edition("ft", "web_front", Story(
        "Challenges facing Macron deepen after street clashes reignite French debt rout",
        "Gap between French and German borrowing costs widens to about 150 basis points as curfews are imposed and the military is readied for further unrest",
        [], "Paris and London", "France", "https://www.ft.com/", ASTRO, "Test photograph", is_free=False),
        [S("Chancellor rules out a rise in capital gains tax", "UK Budget"),
         S("Burnham asks officials for radical options on Thames Water", "Utilities"),
         S("French yields near 24-year high as new protests loom", "Bonds"),
         S("Army front-line vehicle programme faces £2bn cut", "Defence")],
        note="ft.com · headlines (articles paywalled)"),
    "times": Edition("times", "print_front", Story(
        "Labour cuts £2bn from Army front line",
        "Armoured vehicle variants scrapped as defence chiefs warn of gaps in capability",
        ["Mortar, repair and overwatch versions of the Boxer armoured vehicle have been dropped, leaving the army to find other ways to fill gaps in its front-line capability.",
         "Ministers confirmed in answers to parliament that almost two billion pounds had been removed from the programme, which was meant to form the backbone of the mechanised infantry.",
         "Senior officers said the decision would leave units without specialist vehicles for several years, while the Ministry of Defence said the requirement would be met in other ways.",
         "The opposition said the cuts showed the government was not serious about defence, weeks after promising to raise spending as a share of national income.",
         "The Boxer was ordered as a family of variants that would share parts and training. Removing three of them means the army will have to keep older vehicles in service or buy new ones.",
         "Defence sources said the money would be redirected to drones, air defence and long-range missiles, which ministers regard as the lesson of the war in Ukraine.",
         "A former chief of the general staff said the army was being asked to do more with less, and warned that it could no longer field a full division at short notice."],
        "Larisa Brown, Defence Editor", "Defence", "https://www.thetimes.com/", "", is_free=True),
        [S("Curfews across France after nights of rioting", "Europe"),
         S("Hospitals not ready for winter, leaders warn", "Health"),
         S("No increase to capital gains tax in the Budget", "Economy")],
        continued_page=6, note="Printed front page · text by OCR"),
    "indy": Edition("indy", "web_front", Story(
        "Curfew imposed to stop France riots as unrest grips Europe",
        "About 2,400 arrested over the weekend as Paris, Lille and Nice impose overnight restrictions, and Italy prepares troops for the streets of Milan",
        [], "Europe staff", "Europe", "https://www.independent.co.uk/", ASTRO, "Test photograph"),
        [S("Rioters face €20,000 fines under new laws as 500 troops patrol Milan", "Italy"),
         S("The 74-year-old US teacher still held in Russian detention", "Russia"),
         S("Ohio Democrat hits back at attack ad featuring her estranged mother", "US politics")],
        note="independent.co.uk · homepage order"),
    "i": Edition("i", "print_front", Story(
        "Two-child cap on childcare benefits will be lifted by April",
        "Parents on benefits with three or more children set to get help with nursery costs for every child",
        [], "Political staff", "Exclusive", "https://inews.co.uk/", CAT, "Test photograph"),
        [S("Overnight curfews imposed in several cities after a weekend of riots", "France"),
         S("Burnham orders officials to draw up radical options for Thames Water", "Water"),
         S("Chancellor rules out raising capital gains tax", "Budget"),
         S("Health leaders warn hospitals are not ready for another winter crisis", "NHS"),
         S("Doncaster Sheffield Airport set for public ownership", "Transport")],
        note="Print splash · inews.co.uk"),
    "telegraph": Edition("telegraph", "print_front", Story(
        "Labour cuts £2bn from Army front line",
        "Mortar, repair and overwatch versions of the Boxer armoured vehicle are dropped, leaving the Army to find other ways to fill the gaps",
        [], "Defence staff", "Defence", "https://www.telegraph.co.uk/", CAT, "Test photograph"),
        [S("Badenoch warns the Budget could be worse than last year’s", "Politics"),
         S("Curfews across French cities after nights of rioting", "Europe"),
         S("Chancellor rules out a capital gains tax rise", "Economy")],
        note="Print splash · telegraph.co.uk"),
    "newworld": Edition("newworld", "web_front", Story(
        "Europe’s streets erupt as cost-of-living anger spreads from Paris to Milan",
        "Curfews in France and troops in Italy: why the protests matter for Britain’s relationship with its neighbours",
        [], "Foreign staff", "Europe", "https://www.thenewworld.co.uk/", ASTRO, "Test photograph"),
        [S("The Thames Water saga shows the limits of privatisation", "Britain"),
         S("Inside the race to rebuild Ukraine’s power grid before winter", "Ukraine"),
         S("What a Budget without a tax rise on capital gains really means", "Economy")],
        note="thenewworld.co.uk · homepage order"),
    "economist": Edition("economist", "web_front", Story(
        "France’s debt troubles are back, and this time the streets are angry",
        "Curfews, riots and a widening bond spread put Emmanuel Macron’s government under fresh strain",
        [], "Paris", "Europe", "https://www.economist.com/", COFFEE, "Test photograph", is_free=False),
        [S("Britain’s water industry needs more than a rescue", "Britain"),
         S("America resumes intelligence-sharing with Ukraine", "United States"),
         S("Why central banks are pausing rate cuts", "Finance & economics"),
         S("The quiet return of industrial policy in Europe", "Business")],
        note="economist.com · headlines (articles paywalled)"),
    "spectator": Edition("spectator", "web_front", Story(
        "Labour’s defence cuts expose the gap between rhetoric and reality",
        "Ministers promised more for the armed forces. The Boxer decision tells a different story",
        [], "Political staff", "Politics", "https://www.spectator.co.uk/", CAT, "Test photograph"),
        [S("Kemi Badenoch’s warning on the Budget", "Politics"),
         S("France is learning what happens when the money runs out", "Europe"),
         S("Is the NHS ready for winter? Nobody believes it", "Health")],
        note="spectator.co.uk · homepage order"),
}


def edition(key: str) -> Edition:
    return copy.deepcopy(EDITIONS[key])


def states() -> dict[str, Edition]:
    """Named demo states for /demo/<state>.json: one per paper plus stress cases."""
    out = {k: edition(k) for k in EDITIONS}
    ft = edition("ft")
    ft.kind, ft.lead.paragraphs, ft.lead.is_free, ft.continued_page = "print_front", FRANCE_FT, True, 4
    ft.lead.byline, ft.note = "Leila Abboud — Paris", "Printed front page · text by OCR"
    out["ft-print"] = ft
    long = edition("guardian")
    long.lead.headline = ("Chancellor extends fuel duty freeze for another year as borrowing figures come in "
                          "£9bn above forecast and opposition demands answers")
    out["long-headline"] = long
    noimg = edition("guardian")
    noimg.lead.image_url = ""
    out["no-image"] = noimg
    cached = edition("bbc")
    cached.stale = True
    out["cached"] = cached
    return out
