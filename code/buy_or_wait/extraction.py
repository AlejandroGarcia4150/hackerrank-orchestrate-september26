"""Evidence is data: constrained facts cannot change policy or execute commands."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
from .normalization import money,day

MONEY_RE = re.compile(r'\b(INR|IDR|ZAR|USD|EUR)\s+([0-9][0-9,]*(?:\.[0-9]+)?)', re.I)
DATE_RE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')

def message_facts(rows,request):
    facts, audit = [], []
    for row in sorted(rows,key=lambda r:(r['sent_at'],r['message_id'])):
        if row['sent_at'][:10] > request['request_date'] or row['request_id'] not in ('',request['request_id']):
            continue
        text=row['message_text']; lower=text.lower()
        fact={'message_id':row['message_id'],'related_event_id':row['related_event_id'],
              'sent_at':row['sent_at'],'kind':'context'}
        values=[(c.upper(),a.replace(',','')) for c,a in MONEY_RE.findall(text)]
        dates=DATE_RE.findall(text)
        # Source-gated lexical extraction. Instructions to bypass safety are never evaluated.
        if re.search(r'ignore .*instructions|ignore .*balance|override .*rules|reveal .*user|execute .*command|pay.*processing fee|bayar biaya',lower):
            fact['kind']='untrusted_instruction_ignored'
        elif re.search(r'lease increases|sewa menaikkan',lower):
            match=re.search(r'(\d+(?:\.\d+)?)%',lower)
            if match: fact.update(kind='rent_increase',percent=match[1])
        elif row['source_type']=='employer':
            if re.search(r'current seasonal contract has ended|kontrak musiman.*berakhir|your employment has ended|hubungan kerja.*berakhir',lower):
                fact['kind']='salary_ended'
            elif re.search(r'now expected on|kini diperkirakan masuk pada',lower) and dates:
                fact.update(kind='salary_date',date=dates[0])
            elif values and re.search(r'salary|monthly pay|gaji',lower) and not row['related_event_id']:
                fact.update(kind='salary_amount',currency=values[0][0],amount=values[0][1])
                if dates: fact['date']=dates[0]
                if re.search(r'one.time arrears|tunggakan satu kali',lower) and len(values)>1:
                    fact['one_time_extra']=values[1][1]
                if re.search(r'one household employment|salah satu sumber pendapatan kerja',lower):fact['replace_all']=True
                if re.search(r'first salary|gaji pertama',lower):fact['new_employment']=True
                if re.search(r'next salary is reduced|gaji berikutnya.*dikurangi',lower):fact['next_only']=True
                if re.search(r'confirmed base salary|gaji pokok',lower):fact['replace_all']=True
        elif row['source_type']=='service_provider' and values and dates and re.search(r'approved an invoice|menyetujui pembayaran faktur',lower):
            fact.update(kind='confirmed_one_time_income',currency=values[0][0],amount=values[0][1],date=dates[0])
        if row['related_event_id']:
            if re.search(r'payment was received|order was paid|pembayaran.*sudah.*diterima',lower):fact['kind']='event_paid'
            elif re.search(r'cancelled|canceled|dibatalkan',lower):fact['kind']='event_cancelled'
            elif re.search(r'not reached|not.*credited|belum masuk|still processing|masih.*diproses',lower):fact['kind']='unsettled_credit'
            elif re.search(r'no units have been sold|has not been sold|belum dijual',lower):fact['kind']='noncash'
            elif re.search(r'sale.*settled|hasil penjualan.*masuk',lower):fact['kind']='settled_proceeds'
        facts.append(fact)
        audit.append(fact)
    return facts,audit

class Images:
    def __init__(self,dataset,cache_path):
        self.dataset=dataset
        self.cache=json.loads(Path(cache_path).read_text(encoding='utf-8')) if Path(cache_path).exists() else {}
        self.audit=[]

    def extract(self,row,event):
        path=self.dataset.directory/'media/images'/(row['image_id']+'.png')
        record={'image_id':row['image_id'],'related_event_id':row['related_event_id']}
        self.audit.append(record)
        try:
            data=path.read_bytes()
            # Validate file structure and decoding, not just the extension.
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
            digest=hashlib.sha256(data).hexdigest(); record['sha256']=digest
            fact=self.cache.get(digest)
            if fact is None:
                fact=self.ocr(path,event)
            record['facts']=fact
            if fact.get('currency')!=event['currency']:
                raise ValueError('Image currency conflicts with event currency')
            if event['direction']=='credit' and 'net_pay' in fact:
                amount=fact['net_pay']; label='net_pay'
            elif 'due_after' in fact and event['settlement_date']>fact['due_date']:
                amount=fact['due_after']; label='late_amount_due'
            elif 'balance_due' in fact and event['status'] in ('pending','scheduled'):
                amount=fact['balance_due']; label='balance_due'
            else:
                amount=fact.get('total'); label='total'
            result=money(amount)
            record.update(selected_amount=str(result),selected_label=label,status='resolved')
            return result,fact
        except Exception as exc:
            record.update(status='unresolved',error=str(exc))
            return None,{}

    def ocr(self,path,event):
        """Optional local fallback for unseen images; ambiguous candidates fail closed."""
        exe=shutil.which('tesseract')
        if not exe:raise ValueError('Unseen image: install Tesseract or supply a reviewed SHA-256 fact cache')
        result=subprocess.run([exe,str(path),'stdout','--psm','6'],capture_output=True,text=True,check=True,timeout=60)
        text=result.stdout
        currency=set(re.findall(r'\b(?:INR|IDR|USD|ZAR|EUR)\b',text))
        if '₹' in text or re.search(r'\bRupees\b',text,re.I):currency.add('INR')
        if len(currency)!=1:raise ValueError('OCR currency is ambiguous')
        patterns=(['net pay'] if event['direction']=='credit' else ['balance due','amount payable','grand total','total paid','total'])
        for label in patterns:
            candidates=[]
            for line in text.splitlines():
                if re.search(r'\b'+label+r'\b',line,re.I):
                    match=re.search(r'(\d[\d,]*\.\d{2})\s*$',line)
                    if match:candidates.append(match[1])
            if len(set(candidates))==1:
                key='net_pay' if label=='net pay' else 'total'
                return {'currency':currency.pop(),key:candidates[0],
                        'evidence':text,'extraction_method':'local Tesseract; unambiguous labeled amount'}
        raise ValueError('OCR amount is ambiguous; reviewed evidence required')
