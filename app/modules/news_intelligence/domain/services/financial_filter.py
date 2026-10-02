"""
Production-Grade Financial & Market Relevance Classification Engine.

Ensures that 100% of articles processed, ingested, and served by the SentiNews
platform are strictly relevant to financial markets, corporate intelligence,
macro/microeconomics, commodities, currencies, central banks, or market-moving
geopolitical developments.

Strictly filters out non-market noise including:
- Entertainment, celebrity gossip, cinema/box office, and OTT releases.
- Sports, cricket scores, and gaming tournaments.
- Astrology, horoscopes, and zodiac predictions.
- Casual lifestyle, health/recipes, and travel advice.
- Local non-economic crime and municipal accidents.
"""

import re
from typing import List, Optional, Set, Tuple


# ============================================================================
# POSITIVE PATTERNS: Financial, Economic, Market & Corporate Indicators
# ============================================================================

FINANCIAL_PATTERNS_HIGH_CONFIDENCE = [
    # Markets & Indices
    r"\b(nifty|sensex|banknifty|bse|nse|wall\s+street|nasdaq|s&p\s*500|dow\s+jones|ftse|dax|nikkei|hang\s+seng)\b",
    r"\b(stock\s+market|bull\s+market|bear\s+market|market\s+rally|market\s+crash|market\s+correction|equities|shares?)\b",
    r"\b(ipo|ipos|fpo|qip|ofss?|listing\s+gains|delisting|buyback|dividends?|bonus\s+issue|stock\s+split)\b",
    # Corporate Finance & Earnings
    r"\b(earnings|quarterly\s+results|q[1-4]\s+(results|profit|revenue|fy\d+)|ebitda|operating\s+profit|net\s+profit|net\s+loss)\b",
    r"\b(market\s+cap|market\s+capitalization|m-cap|valuation|pe\s+ratio|book\s+value|price-to-earnings)\b",
    r"\b(merger|acquisition|m&a|takeover|joint\s+venture|stake\s+sale|promoter\s+holding|block\s+deal|bulk\s+deal)\b",
    r"\b(fundraising|private\s+equity|venture\s+capital|series\s+[a-f]|valuation\s+round|unicorn|angel\s+investor|bonds?|bond\s+issuance|debentures?|commercial\s+papers?|debt\s+financing|debt\s+refinancing|refinancing|treasury\s+bills?|g-secs?)\b",
    r"\b(bankruptcy|insolvency|nclt|debt\s+restructuring|default|credit\s+rating|crisil|icra|care|fitch|moody'?s|s&p)\b",
    # Macroeconomics & Policy
    r"\b(gdp|gross\s+domestic\s+product|economic\s+growth|recession|stagflation|deflation|inflation|cpi|wpi)\b",
    r"\b(retail\s+inflation|wholesale\s+inflation|core\s+inflation|rate\s+hike|rate\s+cut|interest\s+rates?)\b",
    r"\b(rbi|reserve\s+bank\s+of\s+india|monetary\s+policy|repo\s+rate|reverse\s+repo|crr|slr|mpc)\b",
    r"\b(federal\s+reserve|the\s+fed|fomc|jerome\s+powell|ecb|european\s+central\s+bank|bank\s+of\s+england|bank\s+of\s+japan)\b",
    r"\b(union\s+budget|fiscal\s+deficit|taxation|gst|income\s+tax|corporate\s+tax|capital\s+gains|stt|customs\s+duty|tariffs?)\b",
    r"\b(exports?|imports?|trade\s+deficit|trade\s+balance|current\s+account\s+deficit|cad|balance\s+of\s+payments)\b",
    r"\b(rupee|inr|dollar|usd|dxy|dollar\s+index|forex|foreign\s+exchange|forex\s+reserves|currency\s+depreciation)\b",
    r"\b(pmi|manufacturing\s+pmi|services\s+pmi|iip|industrial\s+production|unemployment\s+rate|jobless\s+claims|non-farm\s+payrolls)\b",
    # Banking, Mutual Funds & Institutional Investors
    r"\b(npa|non-performing\s+assets?|bad\s+loans?|asset\s+quality|net\s+interest\s+margin|nim|credit\s+growth)\b",
    r"\b(mutual\s+funds?|amfi|sip|systematic\s+investment|etfs?|index\s+funds?|reits?|invits?)\b",
    r"\b(fii|fiis|fpi|fpis|dii|diis|institutional\s+investors?|foreign\s+inflows?|outflows?|hedge\s+funds?)\b",
    r"\b(sebi|security\s+and\s+exchange\s+board|irdai|pfrda|cci|competition\s+commission|sec\s+filing)\b",
    # Commodities & Energy
    r"\b(crude\s+oil|brent\s+crude|brent|wti|petroleum|natural\s+gas|lng|opec\+?|oil\s+refinery|fuel\s+prices?)\b",
    r"\b(gold\s+price|silver\s+price|precious\s+metals?|copper|aluminum|iron\s+ore|steel\s+prices?|lithium|coal)\b",
    # Market-Moving Geopolitics, Shipping & Supply Chain
    r"\b(sanctions?|trade\s+war|embargo|export\s+ban|import\s+curbs?|export\s+duty|import\s+duty|tariffs?)\b",
    r"\b(supply\s+chain|shipping\s+disruptions?|shipping\s+crisis|freight\s+rates?|container\s+freight|cargo\s+carriers?)\b",
    r"\b(red\s+sea|strait\s+of\s+hormuz|suez\s+canal|panama\s+canal|cape\s+of\s+good\s+hope)\b",
    r"\b(free\s+trade\s+agreement|fta|bilateral\s+trade|trade\s+deal|de-dollarization|brics\s+currency)\b",
    r"\b(strategic\s+petroleum\s+reserve|defense\s+procurement\s+deal|semiconductor\s+mission|pli\s+scheme)\b",
]

FINANCIAL_PATTERNS_MEDIUM_CONFIDENCE = [
    r"\b(investments?|investors?|capex|capital\s+expenditure|venture|subsidiary|expansion|contracts?|order\s+book)\b",
    r"\b(deal|partnership|collaboration|commercial\s+agreement|supply\s+agreement|memorandum\s+of\s+understanding|mou)\b",
    r"\b(ceo|cfo|md\s+and\s+ceo|managing\s+director|board\s+of\s+directors|executive\s+appointment)\b",
    r"\b(technology\s+spending|cloud\s+migration|ai\s+investment|data\s+center|green\s+hydrogen|clean\s+energy)\b",
    r"\b(telecom\s+spectrum|5g\s+rollout|arpu|power\s+generation|renewable\s+capacity|electric\s+vehicles?|ev\s+sales)\b",
    r"\b(logistics|freight|shipping|cargo|manufacturing|infrastructure|real\s+estate|housing\s+sales)\b",
]

COMPILED_HIGH_CONFIDENCE = [re.compile(p, re.IGNORECASE) for p in FINANCIAL_PATTERNS_HIGH_CONFIDENCE]
COMPILED_MEDIUM_CONFIDENCE = [re.compile(p, re.IGNORECASE) for p in FINANCIAL_PATTERNS_MEDIUM_CONFIDENCE]


# ============================================================================
# NEGATIVE NOISE PATTERNS: Entertainment, Sports, Astrology, Lifestyle, Crime
# ============================================================================

NOISE_PATTERNS = [
    # Entertainment / Bollywood / Cinema
    r"\b(bollywood|hollywood|box\s+office\s+collection|box\s+office|movie\s+review|film\s+review)\b",
    r"\b(trailer\s+release|teaser\s+release|ott\s+release|cinema\s+release|actor|actress|heroine|star\s+cast)\b",
    r"\b(celebrity\s+gossip|red\s+carpet|paparazzi|spotted\s+at\s+airport|dating\s+rumours?|wedding\s+photos?)\b",
    r"\b(bigg\s+boss|k-pop|bts|music\s+video|song\s+release|album\s+drop)\b",
    # Sports (Non-financial)
    r"\b(cricket\s+match|ipl\s+match|test\s+match|t20|odi|world\s+cup\s+final|scorecard|wickets?|sixes|overs?)\b",
    r"\b(football\s+match|premier\s+league|champions\s+league|la\s+liga|fifa|goals?\s+scored|penalty\s+shootout)\b",
    r"\b(tennis\s+tournament|grand\s+slam|wimbledon|badminton|esports\s+tournament|gameplay|pubg|bgmi)\b",
    # Astrology / Horoscope
    r"\b(horoscope|astrology|zodiac\s+sign|kundli|rashifal|daily\s+horoscope|weekly\s+horoscope)\b",
    r"\b(vastu\s+tips?|numerology|tarot\s+card|planetary\s+transit|shani\s+transit|rahu\s+ketu)\b",
    # Casual Lifestyle & Diet
    r"\b(recipe|diet\s+plan|weight\s+loss\s+tips?|skincare\s+routine|haircare|relationship\s+advice)\b",
    r"\b(viral\s+video|funny\s+video|memes?|tourist\s+places|travel\s+destinations\s+to\s+visit)\b",
    # Local Non-Economic Crime & Civic Accidents
    r"\b(murder\s+case|arrested\s+for\s+theft|robbery|burglary|stolen|kidnapping|road\s+accident|hit\s+and\s+run)\b",
    r"\b(waterlogging|potholes|traffic\s+jam|building\s+collapse|fire\s+breaks\s+out)\b",
]

COMPILED_NOISE = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]


def is_financial_or_market_news(
    title: str,
    summary: str = "",
    category: str = "",
    matched_symbols: Optional[List[str]] = None,
) -> Tuple[bool, float, str]:
    """
    Evaluates whether an article is strictly relevant to financial markets, corporate events,
    macro/microeconomics, commodities, currencies, or market-moving geopolitics.

    Args:
        title: Headline of the news article.
        summary: Short abstract or summary of the article.
        category: RSS category if present.
        matched_symbols: List of matched ticker symbols (e.g. ['RELIANCE', 'TCS']).

    Returns:
        Tuple[bool, float, str]:
            - is_relevant: True if article should be ingested and displayed.
            - confidence_score: Estimated relevance score (0.0 to 1.0+).
            - decision_reason: Explanation of classification decision.
    """
    title_text = (title or "").strip()
    summary_text = (summary or "").strip()
    full_text = f"{title_text} {summary_text}"

    if not title_text:
        return False, 0.0, "empty_title"

    # 1. Check Noise / Irrelevant Content
    noise_count = 0
    for noise_pat in COMPILED_NOISE:
        if noise_pat.search(title_text):
            noise_count += 2.0  # Title noise penalized heavily
        elif noise_pat.search(summary_text):
            noise_count += 1.0

    # 2. Check High-Confidence Financial & Market Patterns
    high_financial_matches = 0
    for fin_pat in COMPILED_HIGH_CONFIDENCE:
        if fin_pat.search(title_text):
            high_financial_matches += 2.5
        elif fin_pat.search(summary_text):
            high_financial_matches += 1.0

    # 3. Check Medium-Confidence Financial Patterns
    med_financial_matches = 0
    for fin_pat in COMPILED_MEDIUM_CONFIDENCE:
        if fin_pat.search(title_text):
            med_financial_matches += 1.5
        elif fin_pat.search(summary_text):
            med_financial_matches += 0.75

    # 4. Symbol match bonus
    symbol_bonus = 0.0
    if matched_symbols and len(matched_symbols) > 0:
        symbol_bonus = 3.0

    # 5. Category bonus
    cat_bonus = 0.0
    cat_lower = (category or "").lower()
    if cat_lower in ("markets", "economy", "business", "companies", "corporate", "finance", "industry", "commodities", "forex"):
        cat_bonus = 1.0

    total_financial_score = high_financial_matches + med_financial_matches + symbol_bonus + cat_bonus

    # Decision Logic:
    # If explicit noise dominates with no heavy market context -> REJECT
    if noise_count >= 2.0 and total_financial_score < 3.0:
        return False, 0.0, f"rejected_noise (noise={noise_count}, fin={total_financial_score})"

    # If known ticker matched and noise is low -> ACCEPT
    if symbol_bonus > 0 and noise_count < 2.0:
        return True, max(0.9, min(1.0, total_financial_score / 5.0)), "accepted_symbol_match"

    # High financial keyword density -> ACCEPT
    if high_financial_matches >= 2.0:
        return True, min(1.0, total_financial_score / 4.0), "accepted_high_financial_density"

    # Total score above threshold and exceeds noise -> ACCEPT
    if total_financial_score >= 1.5 and total_financial_score > noise_count:
        return True, min(1.0, total_financial_score / 4.0), "accepted_financial_context"

    return False, total_financial_score / 5.0, f"rejected_low_relevance (score={total_financial_score:.2f})"
