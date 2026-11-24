"""Exact MSCI original-series extension from the official public XLS download."""
from datetime import datetime, timezone
import hashlib
import io
import math
import re
import pandas as pd

URL = 'https://app2.msci.com/products/service/index/indexmaster/downloadLevelData'
NAMES = {
 '984000':'USA Standard (Large+Mid Cap)',
 '982600':'UNITED KINGDOM Standard (Large+Mid Cap)',
 '939200':'JAPAN Standard (Large+Mid Cap)',
 '912400':'CANADA Standard (Large+Mid Cap)',
 '903600':'AUSTRALIA Standard (Large+Mid Cap)',
 '928000':'GERMANY Standard (Large+Mid Cap)',
 '925000':'FRANCE Standard (Large+Mid Cap)',
 '975600':'SWITZERLAND Standard (Large+Mid Cap)',
 '891800':'EM (EMERGING MARKETS) Standard (Large+Mid Cap)',
 '106400':'EMU Standard (Large+Mid Cap)',
 '704856':'WORLD CYCLICAL SECTORS Standard (Large+Mid Cap)',
 '704855':'WORLD DEFENSIVE SECTORS Standard (Large+Mid Cap)',
}
TOLERANCE = 0.00050001


def parse_sheet(sheet, codes, start, end):
    if len(codes) != len(set(codes)) or any(c not in NAMES for c in codes):
        raise ValueError('Unqualified index code')
    if sheet.nrows < 8 or sheet.nrows > 400 or sheet.ncols != len(codes)+1:
        raise ValueError('Unexpected download dimensions')
    if sheet.cell_value(2,0) != 'Index Level :' or sheet.cell_value(2,1) != 'Price':
        raise ValueError('Wrong return basis')
    if sheet.cell_value(3,0) != 'Currency :' or sheet.cell_value(3,1) != 'USD':
        raise ValueError('Wrong currency')
    headers=sheet.row_values(6)
    if headers != ['Date']+[NAMES[c] for c in codes]:
        raise ValueError('Index identity or order mismatch')
    values={c:{} for c in codes};seen=set()
    for i in range(7,sheet.nrows):
        label=sheet.cell_value(i,0)
        # The observed workbook footer starts with an empty row, followed by prose.
        if not isinstance(label,str) or not re.fullmatch(r'[A-Z][a-z]{2} \d{1,2}, \d{4}',label):
            continue
        day=datetime.strptime(label,'%b %d, %Y').date().isoformat()
        if not start <= day <= end or day in seen:
            raise ValueError('Out-of-range or duplicate date')
        seen.add(day)
        for col,code in enumerate(codes,1):
            cell=sheet.cell_value(i,col)
            if cell == '':
                # Official partial-day workbook: preserve absence by index, never fill it.
                continue
            if not isinstance(cell,str) or not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{3}',cell):
                raise ValueError('Invalid download level')
            value=float(cell.replace(',',''))
            if not math.isfinite(value) or value <= 0:raise ValueError('Invalid download value')
            values[code][pd.Timestamp(day)]=value
    if not seen:raise ValueError('No dated levels')
    return {c:pd.Series(v,dtype=float).sort_index() for c,v in values.items()}


def parse_download(blob,codes,start,end):
    if len(blob)>8_000_000 or not blob.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        raise ValueError('Expected bounded original XLS response')
    import xlrd
    book=xlrd.open_workbook(file_contents=blob,on_demand=True)
    try:
        if book.nsheets != 1 or book.sheet_names() != ['History Index']:
            raise ValueError('Unexpected download sheets')
        return parse_sheet(book.sheet_by_index(0),codes,start,end)
    finally:book.release_resources()


def extend(source,primary,end,cache,fetch_fn):
    if source['provider']!='msci' or primary.index.max()>=pd.Timestamp(end):return primary,None
    if source['currency']!='USD' or source['variant']!='STRD' or source['symbol'] not in NAMES:
        raise ValueError('Unqualified MSCI specification')
    start=(pd.Timestamp(end)-pd.Timedelta(days=14)).date().isoformat()
    # Shared only within this refresh. Exceptions are cached to avoid 12 repeated requests.
    if not cache:
        codes=list(NAMES)
        params=dict(output='INDEX_LEVELS',currency_symbol='USD',index_variant='STRD',
                    start_date=start.replace('-',''),end_date=end.replace('-',''),
                    data_frequency='daily',index_codes=','.join(codes))
        try:
            blob=fetch_fn(URL,params)
            cache.update(blob=blob, observed_at=datetime.now(timezone.utc).isoformat(), target=end)
            cache['data']=parse_download(blob,codes,start,end)
        except Exception as ex:
            cache.update(error=type(ex).__name__,target=end)
    if cache.get('target')!=end:raise ValueError('Capture target mismatch')
    if 'error' in cache:raise ValueError('Public download failed: '+cache['error'])
    alt=cache['data'][source['symbol']]
    if pd.Timestamp(end) not in alt.index:return primary,None
    overlap=primary.index.intersection(alt.index)
    if len(overlap)<3:raise ValueError('Insufficient original overlap')
    maximum=float((primary.loc[overlap]-alt.loc[overlap]).abs().max())
    if maximum>TOLERANCE:raise ValueError('Public download differs from original history')
    # Extend only the requested session. Interior and preceding-date gaps remain gaps.
    extra=alt.loc[[pd.Timestamp(end)]]
    record=dict(provider='msci_public_download',symbol=source['symbol'],url=URL,
                currency='USD',variant='STRD',dates=[end],precision_decimals=3,
                observed_at=cache['observed_at'],sha256=hashlib.sha256(cache['blob']).hexdigest(),
                automatic_collection=True,overlap_count=len(overlap),overlap_max_difference=maximum)
    return pd.concat([primary,extra]).sort_index(),dict(record=record,blob=cache['blob'])
