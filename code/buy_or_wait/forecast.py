from collections import defaultdict
from datetime import timedelta
from .normalization import ZERO,money,floor_money

def balances(state, payments=(), changes=()):
    adjustments=dict(changes)
    flows=defaultdict(lambda:ZERO)
    for flow in state['flows']:
        amount=flow['amount'];sid=flow['series_id']
        if sid in adjustments:
            amount=-adjustments[sid]
        flows[flow['date']]+=amount
    for date,amount in payments:flows[date]-=amount
    value=money(state['profile']['current_available_balance'])
    result=[]
    for i in range(90):
        date=state['start']+timedelta(days=i)
        value+=flows[date]
        result.append((date,value))
    return result

def feasible(state,payments,changes=()):
    if state['blockers'] or not payments:return False
    deadline=min(state['end'],__import__('datetime').date.fromisoformat(state['request']['desired_completion_date']))
    if any(d<state['start'] or d>deadline or amount<=ZERO for d,amount in payments):return False
    minimum=money(state['profile']['minimum_balance_to_keep'])
    return money(state['profile']['current_available_balance'])>=minimum and min(v for _,v in balances(state,payments,changes))>=minimum

def capacity(state):
    curve=balances(state);minimum=money(state['profile']['minimum_balance_to_keep'])
    opening=money(state['profile']['current_available_balance'])
    requested=money(state['request']['requested_amount'])
    if state['blockers']:return ZERO,None,curve
    safe=max(ZERO,min(requested,floor_money(min(opening,min(v for _,v in curve))-minimum)))
    # Paying today cannot spend more than opening available cash. Future confirmed
    # income can support future dates, but no earlier baseline breach is permitted.
    earliest=None
    from datetime import date
    deadline=date.fromisoformat(state['request']['desired_completion_date'])
    if opening>=minimum:
        prefix_safe=True
        suffix=[ZERO]*90;current=None
        for i in range(89,-1,-1):
            current=curve[i][1] if current is None else min(current,curve[i][1]);suffix[i]=current
        for i,(d,value) in enumerate(curve):
            if d>deadline:break
            available=opening if i==0 else value
            if prefix_safe and available-minimum>=requested and suffix[i]-minimum>=requested:
                earliest=d;break
            prefix_safe=prefix_safe and value>=minimum
    return safe,earliest,curve
