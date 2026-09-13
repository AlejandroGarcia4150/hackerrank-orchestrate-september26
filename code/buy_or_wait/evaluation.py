import csv,json,re
from collections import Counter
from decimal import Decimal
from .ingestion import OUTPUT_COLUMNS
from .normalization import ZERO,money,day,items,fmt
from .plans import option_schedule

def parse_plan(value):
    if value=='none':return []
    result=[]
    for entry in value.split('|'):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}:\d+(?:\.\d{1,2})?',entry):raise ValueError('Invalid payment format')
        date,amount=entry.split(':');result.append((day(date),money(amount)))
    if result!=sorted(result):raise ValueError('Payments not chronological')
    return result

def parse_changes(value):
    if value=='none':return {}
    result={}
    for entry in value.split('|'):
        fields=entry.split(':')
        if len(fields)==2 and fields[0]=='stop':amount=ZERO
        elif len(fields)==3 and fields[0]=='reduce_to':amount=money(fields[2])
        else:raise ValueError('Invalid spending-change syntax')
        if fields[1] in result:raise ValueError('Conflicting spending changes')
        result[fields[1]]=amount
    if len(result)>3:raise ValueError('Too many spending changes')
    return result

def replay(state,payments,changes):
    """Independent ledger replay; does not call the planner's feasibility function."""
    from datetime import timedelta
    minimum=money(state['profile']['minimum_balance_to_keep'])
    balance=money(state['profile']['current_available_balance'])
    low=balance; safe=balance>=minimum
    for i in range(90):
        date=state['start']+timedelta(days=i)
        for flow in state['flows']:
            if flow['date']==date:
                balance+=-changes[flow['series_id']] if flow['series_id'] in changes else flow['amount']
        balance-=sum((a for d,a in payments if d==date),ZERO)
        low=min(low,balance);safe=safe and balance>=minimum
    return safe,low

def validate_row(row,state,options):
    errors=[]
    def check(condition,message):
        if not condition:errors.append(message)
    r,p=state['request'],state['profile'];req=money(r['requested_amount'])
    try:
        check(list(row)==OUTPUT_COLUMNS,'output schema/order')
        safe=money(row['amount_safe_to_pay']);check(ZERO<=safe<=req,'amount bounds')
        status=row['affordability_status'];method=row['recommended_payment_method']
        check(status in ('affordable_now','affordable_with_plan','affordable_later','not_affordable'),'status enum')
        check(method in ('full_payment','partial_payment','installments','wait','not_recommended'),'method enum')
        payments=parse_plan(row['payment_plan']);changes=parse_changes(row['spending_changes_needed'])
        earliest=day(row['earliest_date_for_full_payment']) if row['earliest_date_for_full_payment'] else None
        if earliest:
            check(state['start']<=earliest<=min(day(r['desired_completion_date']),state['end']),'earliest date range/deadline')
            check(replay(state,[(earliest,req)],{})[0],'earliest date safety')
        if safe>ZERO:
            check(replay(state,[(state['start'],safe)],{})[0],'safe amount replay')
            check(safe<=money(p['current_available_balance'])-money(p['minimum_balance_to_keep']),'opening liquidity')
        if safe<req and not state['blockers']:
            next_amount=safe+Decimal('.01')
            opening_room=money(p['current_available_balance'])-money(p['minimum_balance_to_keep'])
            check(next_amount>opening_room or not replay(state,[(state['start'],next_amount)],{})[0],'safe amount is not maximal')
        if earliest and earliest>state['start']:
            from datetime import timedelta
            # Feasibility can improve only on an inflow day; replay all earlier days.
            for i in range((earliest-state['start']).days):
                d=state['start']+timedelta(days=i)
                if d==state['start'] and req>money(p['current_available_balance'])-money(p['minimum_balance_to_keep']):continue
                if replay(state,[(d,req)],{})[0]:errors.append('earliest date is not earliest');break
        if not earliest and not state['blockers']:
            from datetime import timedelta
            for i in range(min(89,(day(r['desired_completion_date'])-state['start']).days)+1):
                d=state['start']+timedelta(days=i)
                if d==state['start'] and req>money(p['current_available_balance'])-money(p['minimum_balance_to_keep']):continue
                if replay(state,[(d,req)],{})[0]:errors.append('missing feasible earliest date');break
        metadata={s['event_id']:s for s in state['series']}
        for sid,value in changes.items():
            check(sid in metadata,'change targets nonrecurring event')
            if sid not in metadata:continue
            s=metadata[sid];cat=s['category']
            check(cat not in items(p['expense_categories_to_protect']),'protected spending modified')
            check(value<s['amount'],'change does not reduce expense')
            if value==ZERO:
                check(cat in items(p['expense_categories_user_is_willing_to_stop']) and s['flexibility'] in ('stoppable','reducible_or_stoppable'),'stop not permitted')
            else:
                check(cat in items(p['expense_categories_user_is_willing_to_reduce']) and s['flexibility'] in ('reducible','reducible_or_stoppable'),'reduction not permitted')
                check(s['minimum'] is not None and value>=s['minimum'],'reduction below allowed minimum')
        if method=='not_recommended':
            check(not payments and not changes and status=='not_affordable','fallback consistency')
        else:
            check(bool(payments),'recommended plan empty')
            check(not state['blockers'],'plan approved with unresolved obligation')
            for d,a in payments:
                check(a>ZERO and state['start']<=d<=min(day(r['desired_completion_date']),state['end']),'plan date/amount')
            check(replay(state,payments,changes)[0],'90-day plan safety')
            check((method if method!='wait' else 'full_payment') in items(p['payment_methods_user_will_consider']),'method preference')
            if method!='installments':check(sum((a for _,a in payments),ZERO)==req,'plan completeness')
        if method=='full_payment':
            check(payments==[(state['start'],req)],'full payment schedule')
            if not changes:check(safe==req and status=='affordable_now','full payment capacity/status')
            else:check(status=='affordable_with_plan','changed full payment status')
        if method=='partial_payment':
            check(r['allows_partial_payment']=='true' and ZERO<safe<req and earliest is not None,'partial eligibility')
            check(payments==[(state['start'],safe),(earliest,req-safe)] and status=='affordable_with_plan','partial schedule/status')
        if method=='installments':
            check(any(option_schedule(o,r,p)==payments for o in options),'installments do not match supplied option')
            check(status=='affordable_with_plan','installment status')
        if method=='wait':
            check(earliest is not None and earliest>state['start'] and safe<req,'wait eligibility')
            check(payments==[(earliest,req)] and status=='affordable_later','wait schedule/status')
        if status=='affordable_now':check(earliest==state['start'] and safe==req and method=='full_payment','affordable_now consistency')
        check(bool(row['decision_explanation'].strip()),'empty explanation')
        # Explanations are templates whose numeric facts must reconcile with replay.
        if payments:
            minimum_text=f"Projected 90-day minimum {p['home_currency']} {fmt(min(v for _,v in __import__('buy_or_wait.forecast',fromlist=['balances']).balances(state,payments,tuple(changes.items()))))}"
            check(minimum_text in row['decision_explanation'],'explanation minimum mismatch')
        check(f"Safe today before changes: {p['home_currency']} {fmt(safe)}" in row['decision_explanation'],'explanation capacity mismatch')
    except Exception as exc:errors.append(str(exc))
    return errors

def compare(predictions,expected):
    by_id={r['request_id']:r for r in predictions};metrics={};details=[]
    for col in OUTPUT_COLUMNS[1:]:
        pairs=[(by_id[r['request_id']][col],r[col],r['request_id']) for r in expected
               if r['request_id'] in by_id and (r.get(col,'')!='' or col=='earliest_date_for_full_payment')]
        if not pairs:metrics[col]={'evaluated':0};continue
        if col=='decision_explanation':
            metrics[col]={'evaluated':len(pairs),'exact_text_match':sum(a==b for a,b,_ in pairs)/len(pairs),
                          'note':'Wording is not a quality metric; numeric consistency is validated separately.'};continue
        errors=[];matches=0;confusion=Counter();abs_errors=[];rel_errors=[]
        for actual,target,rid in pairs:
            equal=actual==target
            if col=='amount_safe_to_pay':
                diff=abs(money(actual)-money(target));equal=diff<=Decimal('.01');abs_errors.append(diff)
                if money(target)>ZERO:rel_errors.append(diff/money(target))
            elif col=='payment_plan':equal=parse_plan(actual)==parse_plan(target)
            if equal:matches+=1
            else:
                details.append({'request_id':rid,'column':col,'expected':target,'predicted':actual})
            if col in ('affordability_status','recommended_payment_method'):confusion[(target,actual)]+=1
        metric={'evaluated':len(pairs),'accuracy':matches/len(pairs),'matched':matches}
        if abs_errors:
            metric.update(mean_absolute_error=str(sum(abs_errors)/len(abs_errors)),
                          max_absolute_error=str(max(abs_errors)),
                          mean_relative_error=str(sum(rel_errors)/len(rel_errors)) if rel_errors else None,
                          warning='Absolute amounts pool different home currencies; use relative error or per-row differences.')
        if confusion:metric['confusion']=[{'expected':e,'predicted':p,'count':n} for (e,p),n in sorted(confusion.items())]
        metrics[col]=metric
    return {'metrics':metrics,'differences':details}

def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=OUTPUT_COLUMNS,lineterminator='\n');writer.writeheader();writer.writerows(rows)
