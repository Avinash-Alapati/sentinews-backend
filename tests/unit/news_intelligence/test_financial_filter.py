"""
Unit tests for the Financial & Market Relevance Filter.

Validates that financial, economic, commodity, corporate, and market-moving
geopolitical articles are accepted, while entertainment, astrology, sports,
lifestyle, and non-economic civic noise are strictly rejected.
"""

import pytest
from app.modules.news_intelligence.domain.services.financial_filter import (
    is_financial_or_market_news,
)


@pytest.mark.parametrize(
    "title,summary,category,matched_symbols,expected_relevant",
    [
        # 1. Equities, Corporate Results & M&A (Must Accept)
        (
            "Reliance Industries Q3 net profit rises 11% led by retail and oil-to-chemicals growth",
            "Consolidated EBITDA and gross refining margins expanded during the quarter.",
            "Markets",
            ["RELIANCE"],
            True,
        ),
        (
            "TCS secures $1 billion multi-year digital core banking deal in Europe",
            "The IT major will modernize hybrid cloud infrastructure and mainframe applications.",
            "Corporate",
            ["TCS"],
            True,
        ),
        (
            "HDFC Bank announces interim dividend of Rs 19 per share, sets record date",
            "The board of directors approved dividend payout following solid quarterly capital adequacy.",
            "Banking",
            ["HDFCBANK"],
            True,
        ),
        (
            "Swiggy files updated draft red herring prospectus with SEBI for $1.2B IPO",
            "The food delivery platform plans fresh issue and offer for sale for existing investors.",
            "Markets",
            [],
            True,
        ),
        (
            "Zomato acquires Paytm's movie and ticketing business in all-cash deal worth Rs 2,048 crore",
            "The acquisition strengthens 'going-out' segment and expands entertainment commerce valuation.",
            "Corporate",
            ["ZOMATO", "PAYTM"],
            True,
        ),

        # 2. Macroeconomics & Central Banks (Must Accept)
        (
            "RBI Monetary Policy Committee keeps repo rate unchanged at 6.5%, maintains withdrawal of accommodation",
            "Governor highlighted food inflation pressures while forecasting FY25 GDP growth at 7.2%.",
            "Economy",
            [],
            True,
        ),
        (
            "India's retail inflation CPI cools to 3.65% in August, well within RBI target band",
            "Core inflation remained subdued amidst decline in manufactured goods pricing.",
            "Economy",
            [],
            True,
        ),
        (
            "US Federal Reserve slashes benchmark interest rates by 50 bps amid cooling labor market",
            "Chair Jerome Powell signaled gradual path towards neutral monetary policy stance.",
            "Markets",
            [],
            True,
        ),
        (
            "India's forex reserves hit record high of $689 billion, foreign institutional inflows continue",
            "Foreign currency assets and gold reserves contributed to the multi-billion dollar rise.",
            "Economy",
            [],
            True,
        ),
        (
            "Union Budget 2024: Fiscal deficit pegged at 4.9% of GDP, capital expenditure increased to Rs 11.11 lakh crore",
            "Finance minister proposed reforms in capital gains taxation and customs duty revisions.",
            "Economy",
            [],
            True,
        ),

        # 3. Commodities, Currencies & Energy (Must Accept)
        (
            "Brent crude oil surges past $80 per barrel following Middle East supply disruption fears",
            "OPEC+ maintains production curbs as shipping risks through Strait of Hormuz escalate.",
            "Commodities",
            ["BRENT"],
            True,
        ),
        (
            "Gold prices hit all-time high of Rs 76,000 per 10g on central bank buying and safe haven demand",
            "Precious metals extended rally supported by expectations of aggressive Fed rate cuts.",
            "Commodities",
            ["GOLD"],
            True,
        ),
        (
            "Indian Rupee appreciates 12 paise to 83.80 against US Dollar on FII equity purchases",
            "Dollar index eased as Asian currencies rebounded against greenback.",
            "Forex",
            ["USDINR", "DXY"],
            True,
        ),

        # 4. Geopolitics & Global Trade with Market Impact (Must Accept)
        (
            "US imposes fresh sanctions on Russian oil shipping fleet, tightening price cap enforcement",
            "Global tanker freight rates spike as energy refiners seek alternative non-sanctioned vessels.",
            "Economy",
            [],
            True,
        ),
        (
            "Red Sea shipping crisis forces cargo carriers to reroute around Cape of Good Hope, doubling container freight rates",
            "Supply chain delays impact auto parts and electronics imports across European and Asian ports.",
            "Industry",
            [],
            True,
        ),
        (
            "India and UK near finalization of Free Trade Agreement, zero-duty access for textiles and engineering goods",
            "Bilateral trade target set to double by 2030 with relaxed visa norms for business professionals.",
            "Economy",
            [],
            True,
        ),

        # 5. Entertainment & Celebrity Gossip (Must Strictly Reject)
        (
            "Actor Ranbir Kapoor and Alia Bhatt spotted at Mumbai airport ahead of vacation",
            "The celebrity couple arrived in casual outfits and posed for the paparazzi outside terminal 2.",
            "Entertainment",
            [],
            False,
        ),
        (
            "Box office collection: Latest Bollywood movie crosses Rs 100 crore in opening weekend",
            "Fans praise lead actress performance in action thriller film review.",
            "Entertainment",
            [],
            False,
        ),
        (
            "Bigg Boss 18 contestant elimination: Fans shocked by sudden eviction of popular contestant",
            "Drama unfolds as housemates clash during weekend ka vaar episode.",
            "Entertainment",
            [],
            False,
        ),

        # 6. Non-Financial Sports & Gaming (Must Strictly Reject)
        (
            "India vs Australia 1st Test: Virat Kohli scores brilliant century, India lead by 250 runs",
            "Scorecard updates as bowlers take early wickets on day 3 of cricket match.",
            "Sports",
            [],
            False,
        ),
        (
            "Champions League: Real Madrid defeat Bayern Munich 2-1 with last minute goal to reach final",
            "Sensational comeback goal seals victory in thrilling European football clash.",
            "Sports",
            [],
            False,
        ),

        # 7. Astrology & Horoscopes (Must Strictly Reject)
        (
            "Daily Horoscope October 2024: Check your astrological predictions for Aries, Taurus, and Gemini",
            "Planetary transits bring good luck and positive vibrations according to your zodiac sign.",
            "Astrology",
            [],
            False,
        ),
        (
            "Shani transit 2025: Vastu tips to overcome Sade Sati and planetary doshas for Leo",
            "Follow these astrological remedies to invite peace and prosperity into your home.",
            "Astrology",
            [],
            False,
        ),

        # 8. Casual Lifestyle, Diet & Local Civic News (Must Strictly Reject)
        (
            "Top 5 easy weight loss recipes to include in your daily dinner diet plan",
            "Nutritious high-protein salads and low-carb meals for healthy lifestyle transformation.",
            "Lifestyle",
            [],
            False,
        ),
        (
            "Heavy rainfall causes waterlogging and massive traffic jam on Western Express Highway",
            "Commuters stranded for hours as potholes and broken tree branches block arterial roads.",
            "City",
            [],
            False,
        ),
    ],
)
def test_is_financial_or_market_news(
    title: str,
    summary: str,
    category: str,
    matched_symbols: list,
    expected_relevant: bool,
):
    is_relevant, score, reason = is_financial_or_market_news(
        title=title,
        summary=summary,
        category=category,
        matched_symbols=matched_symbols,
    )
    assert is_relevant is expected_relevant, f"Failed for '{title}'. Result: {is_relevant} (Score: {score}, Reason: {reason})"
