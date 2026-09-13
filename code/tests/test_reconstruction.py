import unittest
from types import SimpleNamespace
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
from buy_or_wait.events import Reconstruction
from buy_or_wait.normalization import Rates

def event(eid,desc,when,amount='100',direction='credit',status='settled',category='salary',link=''):
    return {'event_id':eid,'user_id':'u','event_type':'income' if direction=='credit' else 'expense',
      'description':desc,'category':category,'direction':direction,'amount':amount,'currency':'USD',
      'event_date':when,'settlement_date':when,'status':status,'linked_event_id':link,
      'flexibility':'fixed','minimum_allowed_amount':''}

def build(events,messages=()):
    ds=SimpleNamespace(tables={'images':[]},events={e['event_id']:e for e in events},
       profiles={'u':{'home_currency':'USD','current_available_balance':'1000','minimum_balance_to_keep':'100'}},
       by_user={'financial_events':{'u':events},'messages':{'u':list(messages)}})
    engine=Reconstruction(ds,Rates([]),None)
    return engine.build({'user_id':'u','request_id':'r','request_date':'2026-01-01'})

class ReconstructionTests(unittest.TestCase):
    def test_final_payroll_ends_historical_income(self):
        rows=[event(str(i),'Payroll credit',f'2025-{m:02d}-15') for i,m in enumerate((8,9,10,11))]
        rows.append(event('last','Final employer payroll','2025-12-15'))
        self.assertFalse(any(f['amount']>0 for f in build(rows)['flows']))

    def test_pending_credit_ignored_pending_debit_reserved(self):
        rows=[event('credit','Refund','2026-01-15','10000',status='pending'),
              event('debit','Purchase','2026-01-10','80',direction='debit',status='pending',category='shopping')]
        flows=build(rows)['flows']
        self.assertEqual([f['amount'] for f in flows],[D('-80')])

    def test_settled_history_not_reapplied(self):
        rows=[event('expense','One-off invoice','2025-12-20','500',direction='debit',category='housing')]
        self.assertEqual(build(rows)['flows'],[])

    def test_settled_successor_replaces_pending_authorization(self):
        rows=[event('auth','Authorization','2026-01-10','100',direction='debit',status='pending',category='shopping'),
              event('posted','Posted purchase','2025-12-30','100',direction='debit',category='shopping',link='auth')]
        self.assertEqual(build(rows)['flows'],[])

    def test_failed_debit_does_not_remove_valid_retry(self):
        rows=[event('fail','Bill attempt','2025-12-30','100',direction='debit',status='failed',category='utilities'),
              event('retry','Bill retry','2026-01-05','100',direction='debit',status='scheduled',category='utilities',link='fail')]
        self.assertEqual([f['amount'] for f in build(rows)['flows']],[D('-100')])

    def test_approved_invoice_is_not_recurring(self):
        msg={'message_id':'notice','related_event_id':'','request_id':'r','sent_at':'2025-12-31T00:00:00Z',
             'source_type':'service_provider','message_text':'The client approved an invoice payment of USD 900. Settlement is expected on 2026-01-15; other invoices await approval.'}
        flows=build([], [msg])['flows']
        self.assertEqual([(f['date'],f['amount']) for f in flows],[(date(2026,1,15),D('900'))])

    def test_historical_bonus_not_future_salary(self):
        rows=[event(str(i),'Quarterly performance bonus',f'2025-{m:02d}-15','900') for i,m in enumerate((4,7,10))]
        self.assertEqual(build(rows)['flows'],[])

    def test_monthly_expense_continues_and_oneoff_does_not(self):
        rows=[event(str(i),'Rent',f'2025-{m:02d}-02','100',direction='debit',category='rent') for i,m in enumerate((9,10,11,12))]
        rows.append(event('extra','One-off repair','2025-12-05','250',direction='debit',category='rent'))
        self.assertEqual([f['amount'] for f in build(rows)['flows']],[D('-100')]*3)

if __name__=='__main__':unittest.main()
