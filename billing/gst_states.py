"""
GST state / union-territory codes (as used in GSTINs and place of supply),
plus a rough pincode-prefix -> state table used only to PREFILL the state
dropdown at checkout. The customer can always correct the prefilled value.
"""

SUPPLIER_STATE_CODE = '32'  # Kerala

STATES = [
    ('01', 'Jammu and Kashmir'),
    ('02', 'Himachal Pradesh'),
    ('03', 'Punjab'),
    ('04', 'Chandigarh'),
    ('05', 'Uttarakhand'),
    ('06', 'Haryana'),
    ('07', 'Delhi'),
    ('08', 'Rajasthan'),
    ('09', 'Uttar Pradesh'),
    ('10', 'Bihar'),
    ('11', 'Sikkim'),
    ('12', 'Arunachal Pradesh'),
    ('13', 'Nagaland'),
    ('14', 'Manipur'),
    ('15', 'Mizoram'),
    ('16', 'Tripura'),
    ('17', 'Meghalaya'),
    ('18', 'Assam'),
    ('19', 'West Bengal'),
    ('20', 'Jharkhand'),
    ('21', 'Odisha'),
    ('22', 'Chhattisgarh'),
    ('23', 'Madhya Pradesh'),
    ('24', 'Gujarat'),
    ('26', 'Dadra and Nagar Haveli and Daman and Diu'),
    ('27', 'Maharashtra'),
    ('29', 'Karnataka'),
    ('30', 'Goa'),
    ('31', 'Lakshadweep'),
    ('32', 'Kerala'),
    ('33', 'Tamil Nadu'),
    ('34', 'Puducherry'),
    ('35', 'Andaman and Nicobar Islands'),
    ('36', 'Telangana'),
    ('37', 'Andhra Pradesh'),
    ('38', 'Ladakh'),
    ('97', 'Other Territory'),
]

STATE_NAMES = dict(STATES)
STATE_CODES = frozenset(STATE_NAMES)


def state_name(code):
    return STATE_NAMES.get(code or '', '')


def state_label(code):
    """'Karnataka (29)' — the place-of-supply format printed on invoices."""
    name = state_name(code)
    return f'{name} ({code})' if name else ''


def normalise_state_code(value):
    """Accept '29', 29, '9', 'Karnataka' or 'karnataka' -> '29' (or '')."""
    if value is None:
        return ''
    value = str(value).strip()
    if not value:
        return ''
    if value.isdigit():
        code = value.zfill(2)
        return code if code in STATE_CODES else ''
    lowered = value.lower()
    for code, name in STATES:
        if name.lower() == lowered:
            return code
    return ''


# First two (or three) pincode digits -> state code. Approximate: postal
# circles do not follow state borders exactly, so this only prefills.
_PIN3 = {
    '403': '30',                      # Goa
    '682': '32',                      # Kochi (Lakshadweep shares 682 — Kerala is far likelier)
    '605': '34', '607': '33', '609': '34',  # Puducherry pockets
    '533': '37', '673': '32',
    '737': '11',                      # Sikkim
    '744': '35',                      # Andaman & Nicobar
    '160': '04',                      # Chandigarh
    '396': '26', '362': '24',
    '194': '38',                      # Ladakh
    '790': '12', '791': '12', '792': '12',  # Arunachal
    '793': '17', '794': '17',         # Meghalaya
    '795': '14',                      # Manipur
    '796': '15',                      # Mizoram
    '797': '13', '798': '13',         # Nagaland
    '799': '16',                      # Tripura
}
_PIN2 = {
    '11': '07',
    '12': '06', '13': '06',
    '14': '03', '15': '03', '16': '03',
    '17': '02',
    '18': '01', '19': '01',
    '20': '09', '21': '09', '22': '09', '23': '09', '24': '05', '25': '09',
    '26': '05', '27': '09', '28': '09',
    '30': '08', '31': '08', '32': '08', '33': '08', '34': '08',
    '36': '24', '37': '24', '38': '24', '39': '24',
    '40': '27', '41': '27', '42': '27', '43': '27', '44': '27',
    '45': '23', '46': '23', '47': '23', '48': '23',
    '49': '22',
    '50': '36',
    '51': '37', '52': '37', '53': '37',
    '56': '29', '57': '29', '58': '29', '59': '29',
    '60': '33', '61': '33', '62': '33', '63': '33', '64': '33',
    '67': '32', '68': '32', '69': '32',
    '70': '19', '71': '19', '72': '19', '73': '19', '74': '19',
    '75': '21', '76': '21', '77': '21',
    '78': '18',
    '80': '10', '81': '10', '82': '20', '83': '20', '84': '10', '85': '10',
}


def state_from_pincode(pincode):
    digits = ''.join(ch for ch in str(pincode or '') if ch.isdigit())
    if len(digits) != 6:
        return ''
    return _PIN3.get(digits[:3]) or _PIN2.get(digits[:2], '')
