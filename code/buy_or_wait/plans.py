from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations,product
from decimal import Decimal
from .normalization import ZERO,money,day,items,fmt,month_add
from .forecast import balances,feasible,capacity

@dataclass
class Plan:
    method:str
    payments:list
    changes:tuple=()
    option_id:str=''

    def rank(self):
        return (bool(self.changes),sum((a for _,a in self.payments),ZERO),self.payments[0][0],
                len(self.payments),self.option_id,len(self.changes),tuple((k,str(v)) for k,v in self.changes))

def option_schedule(option,request,profile):
    if option['payment_method']!='installments':return None
    if 'installments' not in items(profile['payment_methods_user_will_consider']):return None
    if not profile['max_installment_months']:return None
    n=int(option['number_of_payments']);frequency=int(option['payment_frequency_days'])
    maximum=int(profile['max_installment_months']);first=day(option['first_payment_date'])
    amount=money(option['payment_amount']);total=money(option['total_payable_amount'])
    fee=money(option['financing_fee']);requested=money(request['requested_amount'])
    if n<2 or frequency<=0 or maximum<=0:return None
    schedule=[(first+timedelta(days=i*frequency),amount) for i in range(n)]
    if n>maximum or schedule[-1][0]>=month_add(first,maximum):return None
    # Every quoted installment is retained verbatim. A per-installment rounding
    # difference of at most half a cent is reported, not silently redistributed.
    if abs(amount*n-total)>Decimal('.005')*n:return None
    if abs(total-requested-fee)>Decimal('.01'):return None
    return schedule

def eligible_plans(state,options,safe,earliest,changes=()):
    r,p=state['request'],state['profile'];methods=items(p['payment_methods_user_will_consider'])
    amount=money(r['requested_amount']);start=state['start']
    candidates=[]
    if 'full_payment' in methods and (safe==amount or changes):
        candidates.append(Plan('full_payment',[(start,amount)],changes))
    if r['allows_partial_payment']=='true' and 'partial_payment' in methods and ZERO<safe<amount and earliest is not None and earliest>start:
        candidates.append(Plan('partial_payment',[(start,safe),(earliest,amount-safe)],changes))
    for option in options:
        schedule=option_schedule(option,r,p)
        if schedule:candidates.append(Plan('installments',schedule,changes,option['payment_option_id']))
    if 'full_payment' in methods and earliest and earliest>start and safe<amount:
        candidates.append(Plan('wait',[(earliest,amount)],changes))
    return [plan for plan in candidates if feasible(state,plan.payments,changes)]

def change_choices(state):
    profile=state['profile'];protected=items(profile['expense_categories_to_protect'])
    stop=items(profile['expense_categories_user_is_willing_to_stop'])
    reduce=items(profile['expense_categories_user_is_willing_to_reduce'])
    choices=[]
    for s in state['series']:
        cat=s['category'];flex=s['flexibility'];actions=[]
        if cat in protected or not s['dates']:continue
        if cat in stop and flex in ('stoppable','reducible_or_stoppable'):
            actions.append((s['event_id'],ZERO))
        if cat in reduce and flex in ('reducible','reducible_or_stoppable') and s['minimum'] is not None and s['minimum']<s['amount']:
            actions.append((s['event_id'],s['minimum']))
        if actions:choices.append(actions)
    return choices

def optimize_reductions(state,plan):
    amounts=dict(plan.changes);meta={s['event_id']:s for s in state['series']}
    for sid,value in plan.changes:
        if value==ZERO:continue
        lo=int(value*100);hi=int(meta[sid]['amount']*100)
        while lo<hi:
            middle=(lo+hi+1)//2;amounts[sid]=Decimal(middle)/100
            if feasible(state,plan.payments,tuple(amounts.items())):lo=middle
            else:hi=middle-1
        amounts[sid]=Decimal(lo)/100
    plan.changes=tuple((sid,v) for sid,v in amounts.items() if v<meta[sid]['amount'])
    return plan

def decide(state,options):
    safe,earliest,curve=capacity(state)
    candidates=eligible_plans(state,options,safe,earliest)
    if not candidates and not state['blockers']:
        choices=change_choices(state)
        for count in range(1,min(3,len(choices))+1):
            for subset in combinations(choices,count):
                for changes in product(*subset):
                    candidates.extend(eligible_plans(state,options,safe,earliest,tuple(changes)))
    chosen=min(candidates,key=Plan.rank) if candidates else None
    if chosen and chosen.changes:chosen=optimize_reductions(state,chosen)
    r,p=state['request'],state['profile'];currency=p['home_currency'];amount=money(r['requested_amount'])
    if chosen:
        if chosen.changes or chosen.method in ('partial_payment','installments'):status='affordable_with_plan'
        elif chosen.method=='wait':status='affordable_later'
        else:status='affordable_now'
        projected=balances(state,chosen.payments,chosen.changes)
        low=min(v for _,v in projected)
        explanation=(f"{chosen.method}: completes {currency} {fmt(amount)} by {chosen.payments[-1][0]}; "
                     f"total paid {currency} {fmt(sum((a for _,a in chosen.payments),ZERO))}. "
                     f"Projected 90-day minimum {currency} {fmt(low)} versus protected {p['minimum_balance_to_keep']}. "
                     f"Safe today before changes: {currency} {fmt(safe)}.")
        if chosen.changes:explanation+=' Requires the listed permitted recurring-spending changes.'
        if state['warnings']:explanation+=' See audit for conservative evidence assumptions.'
    else:
        status='not_affordable';projected=curve
        reason='Unresolved obligations prevent a verifiable safe plan.' if state['blockers'] else 'No accepted complete plan meets the deadline and the 90-day minimum-balance constraint.'
        explanation=f'{reason} Safe today before changes: {currency} {fmt(safe)}.'
    output={
        'request_id':r['request_id'],'amount_safe_to_pay':fmt(safe),'affordability_status':status,
        'recommended_payment_method':chosen.method if chosen else 'not_recommended',
        'payment_plan':'|'.join(f'{d}:{fmt(a)}' for d,a in chosen.payments) if chosen else 'none',
        'earliest_date_for_full_payment':str(earliest) if earliest else '',
        'spending_changes_needed':'|'.join(f'stop:{sid}' if v==ZERO else f'reduce_to:{sid}:{fmt(v)}' for sid,v in chosen.changes) if chosen and chosen.changes else 'none',
        'decision_explanation':explanation}
    return output,chosen,projected
