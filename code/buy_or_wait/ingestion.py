import csv, hashlib, io, json
from collections import Counter, defaultdict
from pathlib import Path
from .normalization import day, money

OUTPUT_COLUMNS = ['request_id','amount_safe_to_pay','affordability_status',
                  'recommended_payment_method','payment_plan','earliest_date_for_full_payment',
                  'spending_changes_needed','decision_explanation']
INPUT_COLUMNS = ['request_id','user_id','request_date','request_type','requested_amount',
                 'desired_completion_date','allows_partial_payment','request_text']
REQUIRED = {
 'requests': INPUT_COLUMNS,
 'financial_profiles': ['user_id','home_currency','current_available_balance','minimum_balance_to_keep',
   'financial_priorities','expense_categories_to_protect','expense_categories_user_is_willing_to_reduce',
   'expense_categories_user_is_willing_to_stop','payment_methods_user_will_consider','max_installment_months'],
 'financial_events': ['event_id','user_id','event_type','description','category','direction','amount','currency',
   'event_date','settlement_date','status','linked_event_id','flexibility','minimum_allowed_amount'],
 'exchange_rates':['rate_date','from_currency','to_currency','rate'],
 'request_payment_options':['payment_option_id','request_id','payment_method','payment_amount','number_of_payments',
   'first_payment_date','payment_frequency_days','financing_fee','total_payable_amount'],
 'messages':['message_id','user_id','request_id','related_event_id','sent_at','source_type','message_text'],
 'images':['image_id','user_id','request_id','related_event_id'],
}

def read_csv(path, report):
    data = path.read_bytes()
    encoding = 'utf-8-sig'
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'cp1252'
        text = data.decode(encoding)
    dialect = csv.Sniffer().sniff(text[:16384], delimiters=',;\t|')
    reader = csv.reader(io.StringIO(text), dialect=dialect, strict=True)
    columns = next(reader)
    if len(set(columns)) != len(columns):
        raise ValueError(f'{path.name}: duplicate column names')
    rows, malformed = [], []
    try:
        for row in reader:
            if len(row) != len(columns):
                malformed.append({'line':reader.line_num,'field_count':len(row)})
            else:
                rows.append(dict(zip(columns,row)))
    except csv.Error as exc:
        malformed.append({'line':reader.line_num,'error':str(exc)})
    counts = Counter(tuple(r.values()) for r in rows)
    report[path.name] = {'sha256':hashlib.sha256(data).hexdigest(),'encoding':encoding,
        'delimiter':dialect.delimiter,'columns':columns,'rows':len(rows),
        'empty_values':{k:sum(r[k]=='' for r in rows) for k in columns},
        'identical_duplicate_rows':sum(v-1 for v in counts.values()), 'malformed_rows':malformed}
    if malformed:
        raise ValueError(f'{path.name}: malformed CSV rows; see ingestion report')
    for column in columns:
        values = [r[column] for r in rows if r[column]]
        inferred = 'string'
        try:
            for value in values: money(value)
            if values: inferred = 'decimal'
        except Exception: pass
        report[path.name].setdefault('inferred_types',{})[column]=inferred
    return rows

class Dataset:
    def __init__(self, directory, evaluation_dir):
        self.directory, self.report, self.tables = Path(directory), {}, {}
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        try:
            for name, cols in REQUIRED.items():
                rows = read_csv(self.directory / (name+'.csv'), self.report)
                actual = self.report[name+'.csv']['columns']
                if set(cols)-set(actual): raise ValueError(f'{name}: missing columns {set(cols)-set(actual)}')
                self.tables[name]=rows
            for name in ('sample_requests','output'):
                if (self.directory/(name+'.csv')).exists():
                    self.tables[name]=read_csv(self.directory/(name+'.csv'), self.report)
            if 'output' in self.tables and self.report['output.csv']['columns'] != OUTPUT_COLUMNS:
                raise ValueError('Output template schema differs from supported problem schema')
            spec = self.directory.parent/'problem_statement.md'
            if not spec.exists(): raise ValueError('problem_statement.md is required')
            contents = spec.read_text(encoding='utf-8')
            if any('`'+col+'`' not in contents for col in OUTPUT_COLUMNS):
                raise ValueError('Unsupported problem_statement.md output schema')
            self.report['problem_statement.md']={'sha256':hashlib.sha256(spec.read_bytes()).hexdigest()}
            for table, key in [('requests','request_id'),('financial_profiles','user_id'),('financial_events','event_id'),
                               ('images','image_id'),('messages','message_id'),('request_payment_options','payment_option_id')]:
                seen = {}
                for row in self.tables[table]:
                    ident=row[key]
                    if not ident: raise ValueError(f'{table}: blank identifier')
                    if ident in seen and (row!=seen[ident] or table!='financial_events'):
                        raise ValueError(f'{table}: duplicate identifier {ident}')
                    seen[ident]=row
                self.tables[table]=list(seen.values())
            self.profiles = {r['user_id']:r for r in self.tables['financial_profiles']}
            self.events = {r['event_id']:r for r in self.tables['financial_events']}
            all_requests = self.tables['requests']+self.tables.get('sample_requests',[])
            requests={r['request_id']:r for r in all_requests}
            for r in all_requests:
                if r['user_id'] not in self.profiles: raise ValueError('Orphan request user')
                day(r['request_date']); day(r['desired_completion_date']); money(r['requested_amount'])
                if r['allows_partial_payment'] not in ('true','false'): raise ValueError('Invalid boolean')
            for r in self.tables['financial_events']:
                if r['user_id'] not in self.profiles: raise ValueError('Orphan event user')
                day(r['event_date'])
                if r['settlement_date']:day(r['settlement_date'])
                elif r['status']=='settled':raise ValueError('Settled event missing settlement date')
                if r['amount']: money(r['amount'])
                link=r['linked_event_id']
                if link and (link not in self.events or self.events[link]['user_id']!=r['user_id']):
                    raise ValueError('Invalid or cross-user lifecycle link')
            for name in ('messages','images'):
                for r in self.tables[name]:
                    if r['user_id'] not in self.profiles: raise ValueError('Orphan evidence user')
                    if r['request_id'] and (r['request_id'] not in requests or requests[r['request_id']]['user_id']!=r['user_id']):
                        raise ValueError('Invalid or cross-user evidence request')
                    if r['related_event_id'] and (r['related_event_id'] not in self.events or self.events[r['related_event_id']]['user_id']!=r['user_id']):
                        raise ValueError('Invalid or cross-user evidence event')
            for r in self.tables['request_payment_options']:
                if r['request_id'] not in requests: raise ValueError('Orphan payment option')
            self.by_user={}
            for name in ('financial_events','messages','images'):
                index=defaultdict(list)
                for r in self.tables[name]:index[r['user_id']].append(r)
                self.by_user[name]=index
            self.options=defaultdict(list)
            for r in self.tables['request_payment_options']:self.options[r['request_id']].append(r)
        finally:
            (evaluation_dir/'ingestion.json').write_text(json.dumps(self.report,indent=2),encoding='utf-8')
