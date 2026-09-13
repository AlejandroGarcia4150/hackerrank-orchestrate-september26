from collections import defaultdict,Counter
from datetime import timedelta
from decimal import Decimal
from statistics import median
import re
from .normalization import ZERO, money,day,rounded,month_add,items
from .extraction import message_facts

VARIABLE_CATEGORIES={'groceries','transport','dining'}
NONRECURRING_CREDIT=re.compile(r'bonus|commission|arrears|prize|reimbursement|proceeds|valuation|project|contract payment|invoice|freelance|independent|platform|app earnings|marketplace|retainer|final employer|prorated',re.I)
REGULAR_CREDIT=re.compile(r'salary|payroll|wages',re.I)

def cadence(rows):
    dates=sorted(set(e['date'] for e in rows))
    if len(dates)<3:return None
    months={d.year*12+d.month for d in dates}
    gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
    if len(months)==len(dates) and max(d.day for d in dates)-min(d.day for d in dates)<=3 and 27<=median(gaps)<=32:
        return ('monthly',int(median(d.day for d in dates)))
    gap=int(median(gaps))
    if 2<=gap<=35 and sum(abs(n-gap)<=1 for n in gaps)/len(gaps)>=.75:
        return ('days',gap)
    return None

def next_dates(last,pattern,start,end):
    when=last
    while True:
        when=month_add(when,1,pattern[1]) if pattern[0]=='monthly' else when+timedelta(days=pattern[1])
        if when>end:return
        if when>=start:yield when

class Reconstruction:
    def __init__(self,dataset,rates,images):
        self.dataset,self.rates,self.images=dataset,rates,images
        self.image_values={};self.image_facts={};self.normalized={}
        self.image_errors={}
        for row in dataset.tables['images']:
            event=dataset.events.get(row['related_event_id'])
            if not event:continue
            amount,fact=images.extract(row,event)
            if amount is None:self.image_errors[event['event_id']]='Unresolved image amount'
            else:
                eid=event['event_id']
                if eid in self.image_values and self.image_values[eid]!=amount:
                    self.image_errors[eid]='Conflicting image amounts'
                self.image_values[eid]=amount;self.image_facts[eid]=fact

    def build(self,request):
        uid=request['user_id']; start=day(request['request_date']);end=start+timedelta(days=89)
        profile=self.dataset.profiles[uid];home=profile['home_currency']
        facts, message_audit=message_facts(self.dataset.by_user['messages'][uid],request)
        warnings=[]; blockers=[]; events=[]; ignored=[]
        def convert(amount,currency,when,source,critical=False):
            try:return self.rates.convert(amount,currency,home,when)
            except ValueError as exc:
                warnings.append(f'{source}: {exc}')
                if critical:blockers.append(f'{source}: cannot bound expense without dated FX')
                return None
        edits={f['related_event_id']:f for f in facts if f['related_event_id'] and f['kind'] in ('event_paid','event_cancelled','unsettled_credit','noncash')}
        raw=self.dataset.by_user['financial_events'][uid]
        # Links alone do not cancel valid cash flows. Only a same-direction settled
        # successor replaces an unposted predecessor in that lifecycle.
        superseded=set()
        for r in raw:
            if r['status']=='settled' and r['linked_event_id']:
                old=self.dataset.events[r['linked_event_id']]
                if old['status'] in ('pending','scheduled') and old['direction']==r['direction']:
                    superseded.add(old['event_id'])
        for source in raw:
            r=dict(source); eid=r['event_id']
            edit=edits.get(eid,{})
            if edit.get('kind')=='event_cancelled':r['status']='cancelled'
            if edit.get('kind')=='event_paid':
                r['status']='settled'
                r['settlement_date']=self.image_facts.get(eid,{}).get('document_date',edit['sent_at'][:10])
            if r['status'] in ('failed','cancelled','unrealized') or eid in superseded:
                ignored.append({'event_id':eid,'reason':'noncash, invalid, or superseded'});continue
            if r['direction']=='credit' and (r['status']=='pending' or edit.get('kind') in ('unsettled_credit','noncash')):
                ignored.append({'event_id':eid,'reason':'unconfirmed credit'});continue
            date=day(r['settlement_date']); event_date=day(r['event_date'])
            if event_date>end and date>end:continue
            amount=money(r['amount']) if r['amount'] else self.image_values.get(eid)
            if eid in self.image_errors or amount is None:
                warnings.append(f'{eid}: unresolved amount')
                if r['direction']=='debit' and (r['status']!='settled' or date>=start):blockers.append(f'{eid}: unresolved obligation')
                continue
            historical=r['status']=='settled' and date<start
            value=convert(amount,r['currency'],date,eid,critical=r['direction']=='debit' and not historical)
            if value is None:continue
            if r['direction'] not in ('debit','credit'):raise ValueError('Unknown cash direction')
            if r['status'] not in ('settled','pending','scheduled'):raise ValueError('Unknown event cash state')
            r.update(date=date,value=value,source_amount=amount,historical=historical,image=eid in self.image_facts)
            events.append(r)

        flows=[];series=[]
        def flow(date,value,source,kind,series_id=None):
            if start<=date<=end:
                flows.append({'date':date,'amount':value,'source':source,'kind':kind,'series_id':series_id})

        # All posted history is already reflected in opening available balance.
        # Unposted overdue debits are reserved on day zero.
        future=[]
        for e in events:
            if not e['historical']:
                when=max(start,e['date'])
                future.append(e)
                flow(when,e['value'] if e['direction']=='credit' else -e['value'],e['event_id'],'explicit')

        history=[e for e in events if e['historical'] and e['direction']=='debit' and not e['linked_event_id'] and not e['image']]
        groups=defaultdict(list)
        for e in history:
            # Variable merchant names do not imply different grocery or travel budgets.
            if e['category'] in VARIABLE_CATEGORIES:
                key=(e['category'],e['flexibility'],e['currency'])
            else:
                key=(e['category'],e['description'],e['currency'],e['date'].day)
            groups[key].append(e)
        for key,rows in sorted(groups.items()):
            rows.sort(key=lambda e:(e['date'],e['event_id']))
            pattern=cadence(rows)
            if not pattern:continue
            # A stopped historical series is not perpetuated indefinitely.
            age=(start-rows[-1]['date']).days
            if age>(62 if pattern[0]=='monthly' else pattern[1]*2):
                warnings.append(f"{rows[-1]['event_id']}: stale recurring expense retained conservatively")
            latest=rows[-1];cat=latest['category'];values=[r['value'] for r in rows[-3:]]
            amount=max(values) if len(set(values))>1 else values[-1]
            for f in facts:
                if f['kind']=='rent_increase' and cat=='rent':
                    amount=rounded(amount*(Decimal('1')+money(f['percent'])/100))
            sid=rows[0]['event_id']
            lower=money(latest['minimum_allowed_amount']) if latest['minimum_allowed_amount'] else None
            if lower is not None:lower=convert(lower,latest['currency'],latest['date'],sid)
            meta={'event_id':sid,'category':cat,'description':latest['description'],'amount':amount,
                  'minimum':lower,'flexibility':latest['flexibility'],'pattern':pattern,
                  'history_ids':[r['event_id'] for r in rows], 'last_date':latest['date'], 'dates':[]}
            for when in next_dates(latest['date'],pattern,start,end):
                matching=[e for e in future if e['direction']=='debit' and e['category']==cat
                          and e['description']==latest['description'] and
                          (e['date'].year,e['date'].month)==(when.year,when.month)]
                if matching and pattern[0]=='monthly':continue
                # Never carry a foreign rate to a different projected settlement date.
                current=amount
                if latest['currency']!=home:
                    current=convert(max(r['source_amount'] for r in rows[-3:]),latest['currency'],when,sid,True)
                if current is not None:
                    flow(when,-current,sid,'recurring_expense',sid);meta['dates'].append(when)
            series.append(meta)

        salaries=[e for e in events if e['historical'] and e['direction']=='credit' and e['category']=='salary'
                  and REGULAR_CREDIT.search(e['description']) and not NONRECURRING_CREDIT.search(e['description'])
                  and not e['linked_event_id'] and not e['image']]
        salary_groups=defaultdict(list)
        for e in salaries:salary_groups[e['description']].append(e)
        current=[]
        for desc,rows in salary_groups.items():
            rows.sort(key=lambda e:e['date'])
            if (start-rows[-1]['date']).days<=62 and (cadence(rows) or len(rows)==1 and re.search('new employer|returning|first.job',desc,re.I)):
                current.append(rows[-1])
        # Old and new payroll descriptions are a change of employer, not two salaries.
        if any(re.search('new employer|returning',e['description'],re.I) for e in current):
            current=[e for e in current if not re.search('previous employer|before leave',e['description'],re.I)]
        final_payroll=[e for e in events if e['direction']=='credit' and re.search(r'final employer payroll|final salary|final settlement',e['description'],re.I)]
        if final_payroll:
            cutoff=max(e['date'] for e in final_payroll)
            current=[e for e in current if e['date']>cutoff]
        salary=sum((e['value'] for e in current),ZERO)
        source_salary=None; salary_currency=home
        payday=max((e['date'].day for e in current),default=None)
        first=None; extra=ZERO; salary_source='recurring salary history'; ended=False
        for f in facts:
            if f['kind']=='salary_ended':salary=ZERO;ended=True
            elif f['kind']=='salary_amount':
                source_salary=money(f['amount']);salary_currency=f['currency'];ended=False
                salary_source=f['message_id']
                if f.get('date'):
                    first=day(f['date']);payday=first.day
                elif payday:
                    first=start.replace(day=min(payday,28)) if payday<=28 else month_add(start,0,payday)
                    if first<start:first=month_add(first,1,payday)
                else:
                    scheduled=[e for e in future if e['direction']=='credit' and e['category']=='salary']
                    if scheduled:first=min(e['date'] for e in scheduled);payday=first.day
                if first:salary=convert(source_salary,salary_currency,first,salary_source) or ZERO
                else:
                    salary=ZERO;warnings.append(f"{salary_source}: confirmed salary lacks a supported payment date")
                extra=money(f['one_time_extra']) if f.get('one_time_extra') else ZERO
            elif f['kind']=='salary_date':
                first=day(f['date']);payday=first.day;salary_source=f['message_id']
            elif f['kind']=='confirmed_one_time_income':
                when=day(f['date']);value=convert(money(f['amount']),f['currency'],when,f['message_id'])
                if value is not None and not any(e['direction']=='credit' and e['date']==when and e['value']==value for e in future):
                    flow(when,value,f['message_id'],'confirmed_invoice')
        scheduled_salary=[e for e in future if e['category']=='salary' and e['direction']=='credit'
                          and REGULAR_CREDIT.search(e['description']) and not NONRECURRING_CREDIT.search(e['description'])]
        if not ended and salary==ZERO and scheduled_salary and source_salary is None:
            confirmed=min(scheduled_salary,key=lambda e:e['date'])
            salary=confirmed['value'];first=confirmed['date'];payday=first.day;salary_source=confirmed['event_id']
            source_salary=confirmed['source_amount'];salary_currency=confirmed['currency']
        if salary>ZERO and payday and not ended:
            if first is None:
                first=month_add(start,0,payday)
                if first<start:first=month_add(first,1,payday)
            when=first; added_extra=False
            while when<=end:
                if when>=start:
                    value=salary
                    if source_salary is not None and salary_currency!=home:
                        value=convert(source_salary,salary_currency,when,salary_source)
                    elif source_salary is None and any(e['currency']!=home for e in current):
                        converted=[convert(e['source_amount'],e['currency'],home_date,salary_source)
                                   for e in current for home_date in [when]]
                        value=sum((v for v in converted if v is not None),ZERO)
                    # Explicit salary rows are amended by the latest payroll notice.
                    matching=[e for e in scheduled_salary if (e['date'].year,e['date'].month)==(when.year,when.month)]
                    if matching:
                        ids={e['event_id'] for e in matching}
                        flows[:]=[f for f in flows if f['source'] not in ids]
                    if value is not None:flow(when,value,salary_source,'confirmed_recurring_salary')
                    if extra>ZERO and not added_extra:
                        value_extra=convert(extra,salary_currency,when,salary_source)
                        if value_extra is not None:flow(when,value_extra,salary_source,'one_time_payroll_adjustment')
                        added_extra=True
                when=month_add(when,1,payday)
        elif ended:
            # Future regular salary contradicted by a newer termination notice is excluded.
            ids={e['event_id'] for e in scheduled_salary}
            flows[:]=[f for f in flows if f['source'] not in ids]
        return {'request':request,'profile':profile,'start':start,'end':end,'flows':flows,'series':series,
                'warnings':sorted(set(warnings)),'blockers':sorted(set(blockers)),
                'message_facts':message_audit,'ignored_events':ignored,
                'assumptions':['Opening balance already contains all historical settled transactions.',
                    'Variable recurring expense uses the maximum of its last three observations.',
                    'Monthly cadence requires history or explicit ongoing employment confirmation.',
                    'Only 90 daily closing balances are modeled; intraday ordering is unavailable.']}
