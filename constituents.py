"""
Approximate index constituents and weights, for the popover on index cards.
Reference only, hand-maintained, and labelled "approximate" wherever shown.
"""

# Each entry: list of (name, weight%) tuples — approximate, for reference only
CONSTITUENTS = {
    "^GSPC": [
        ("Apple", 7.0), ("Microsoft", 6.5), ("Nvidia", 6.0), ("Amazon", 3.8),
        ("Meta", 2.5), ("Alphabet", 4.0), ("Berkshire", 1.7), ("Broadcom", 1.8),
        ("Tesla", 1.5), ("JPMorgan", 1.4),
    ],
    "^IXIC": [
        ("Apple", 8.5), ("Microsoft", 8.0), ("Nvidia", 7.5), ("Amazon", 5.0),
        ("Meta", 4.5), ("Alphabet", 5.0), ("Broadcom", 3.5), ("Tesla", 2.5),
        ("Costco", 2.0), ("ASML", 1.8),
    ],
    "^NSEI": [
        ("HDFC Bank", 13.0), ("Reliance", 10.0), ("ICICI Bank", 8.0), ("Infosys", 6.0),
        ("TCS", 4.0), ("Bharti Airtel", 4.0), ("ITC", 3.0), ("L&T", 3.0),
        ("Axis Bank", 3.0), ("HUL", 2.0),
    ],
    "^KS11": [
        ("Samsung Electronics", 22.0), ("SK Hynix", 6.0), ("LG Energy", 3.0), ("Hyundai Motor", 3.0),
        ("Kakao", 2.0), ("Naver", 2.0), ("Samsung SDI", 2.0), ("Posco", 1.5),
        ("KB Financial", 1.5), ("Shinhan", 1.5),
    ],
    "^FTSE": [
        ("Shell", 8.0), ("AstraZeneca", 8.0), ("HSBC", 6.0), ("Unilever", 4.0),
        ("BP", 3.0), ("Rio Tinto", 3.0), ("Diageo", 3.0), ("GSK", 3.0),
        ("BHP", 2.5), ("Rolls-Royce", 2.5),
    ],
    "^GDAXI": [
        ("SAP", 14.0), ("Siemens", 8.0), ("Allianz", 7.0), ("Deutsche Telekom", 6.0),
        ("Infineon", 4.0), ("BASF", 3.0), ("BMW", 3.0), ("Bayer", 3.0),
        ("Deutsche Bank", 3.0), ("Volkswagen", 2.5),
    ],
    "^STOXX50E": [
        ("ASML", 8.0), ("SAP", 5.0), ("LVMH", 5.0), ("Schneider Electric", 4.0),
        ("Siemens", 4.0), ("Sanofi", 3.5), ("TotalEnergies", 3.5), ("Airbus", 3.5),
        ("BNP Paribas", 3.0), ("Stellantis", 2.0),
    ],
    "^N225": [
        ("Fast Retailing", 10.0), ("Tokyo Electron", 6.0), ("SoftBank", 5.0), ("Keyence", 4.0),
        ("Fanuc", 3.0), ("Toyota", 2.5), ("Sony", 2.0), ("Mitsubishi UFJ", 1.5),
        ("Nintendo", 1.5), ("Honda", 1.0),
    ],
    "^NSEBANK": [
        ("HDFC Bank", 30.0), ("ICICI Bank", 24.0), ("Kotak Bank", 10.0), ("Axis Bank", 10.0),
        ("SBI", 9.0), ("IndusInd Bank", 4.0), ("Bank of Baroda", 2.0), ("PNB", 2.0),
        ("IDFC First", 2.0), ("Federal Bank", 1.5),
    ],
    "^CNXIT": [
        ("TCS", 24.0), ("Infosys", 23.0), ("HCL Tech", 14.0), ("Wipro", 9.0),
        ("Tech Mahindra", 6.0), ("LTIMindtree", 6.0), ("Persistent", 4.0), ("Mphasis", 3.0),
        ("Coforge", 2.0), ("KPIT Tech", 2.0),
    ],
    "NSE:NIFTY AUTO": [
        ("Maruti", 22.0), ("M&M", 20.0), ("Tata Motors", 12.0), ("Bajaj Auto", 10.0),
        ("Hero MotoCorp", 8.0), ("Eicher Motors", 7.0), ("TVS Motor", 6.0), ("Bosch", 4.0),
        ("MRF", 3.0), ("Ashok Leyland", 2.0),
    ],
    "NSE:NIFTY ENERGY": [
        ("Reliance", 35.0), ("ONGC", 15.0), ("NTPC", 12.0), ("PowerGrid", 8.0),
        ("BPCL", 5.0), ("IOC", 5.0), ("Tata Power", 4.0), ("Adani Green", 4.0),
        ("Coal India", 4.0), ("GAIL", 3.0),
    ],
    "NSE:NIFTY MID SELECT": [
        ("Trent", 5.0), ("Persistent", 4.5), ("Tube Investments", 4.0), ("Kalyan Jewellers", 3.5),
        ("Oberoi Realty", 3.5), ("Bharat Forge", 3.0), ("Cummins India", 3.0), ("JSW Energy", 3.0),
        ("KPIT Tech", 3.0), ("Voltas", 2.5),
    ],
    "NSE:NIFTY INDIA DEFENCE": [
        ("HAL", 30.0), ("BEL", 20.0), ("Bharat Dynamics", 10.0), ("Mazagon Dock", 8.0),
        ("BEML", 7.0), ("Garden Reach", 7.0), ("Cochin Shipyard", 6.0), ("Data Patterns", 4.0),
        ("Paras Defence", 2.0), ("DCX Systems", 2.0),
    ],
}
