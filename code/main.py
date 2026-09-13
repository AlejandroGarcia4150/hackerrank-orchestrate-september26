#!/usr/bin/env python3
import argparse,csv,hashlib,json,shutil,sys
from pathlib import Path
from collections import Counter
from buy_or_wait.ingestion import Dataset,OUTPUT_COLUMNS,INPUT_COLUMNS
from buy_or_wait.normalization import Rates
from buy_or_wait.extraction import Images
from buy_or_wait.events import Reconstruction
from buy_or_wait.plans import decide
from buy_or_wait.evaluation import validate_row,compare,write_csv

def main():
    code_dir=Path(__file__).resolve().parent
    root=code_dir.parent if code_dir.name=='code' else code_dir
    parser=argparse.ArgumentParser(description='Buy or Wait? deterministic local pipeline')
    parser.add_argument('--dataset',type=Path,default=root/'dataset')
    parser.add_argument('--output',type=Path,default=root/'output.csv')
    parser.add_argument('--evaluation-dir',type=Path,default=Path(__file__).resolve().parent/'evaluation')
    parser.add_argument('--evidence-cache',type=Path,default=Path(__file__).resolve().parent/'evidence/visual_facts.json')
    parser.add_argument('--write-dataset-output',action='store_true',help='Also write dataset/output.csv, as requested by the project owner')
    args=parser.parse_args();out=args.evaluation_dir;out.mkdir(parents=True,exist_ok=True)
    dataset=Dataset(args.dataset,out);rates=Rates(dataset.tables['exchange_rates'])
    images=Images(dataset,args.evidence_cache);engine=Reconstruction(dataset,rates,images)
    all_errors=[];final=[];sample=[];states=[]
    with (out/'audit.jsonl').open('w',encoding='utf-8') as audit:
        for split,requests in [('final',dataset.tables['requests']),('sample',dataset.tables.get('sample_requests',[]))]:
            for raw in requests:
                # Targets are stripped at the boundary, before reconstruction or planning.
                request={k:raw[k] for k in INPUT_COLUMNS}
                state=engine.build(request);options=dataset.options[request['request_id']]
                row,plan,curve=decide(state,options)
                errors=validate_row(row,state,options)
                if errors:all_errors.append({'split':split,'request_id':row['request_id'],'errors':errors})
                audit.write(json.dumps({'split':split,'request_id':row['request_id'],
                    'output':row,'selected_option_id':plan.option_id if plan else None,
                    'warnings':state['warnings'],'blockers':state['blockers'],'message_facts':state['message_facts'],
                    'assumptions':state['assumptions'],'series':state['series'],'flows':state['flows'],
                    'daily_balances':curve,'ignored_events':state['ignored_events'],'validation_errors':errors},default=str,ensure_ascii=False)+'\n')
                (final if split=='final' else sample).append(row)
                if split=='final':states.append(state)
    expected_ids=[r['request_id'] for r in dataset.tables['requests']]
    actual_ids=[r['request_id'] for r in final]
    if actual_ids!=expected_ids or len(set(actual_ids))!=len(actual_ids):raise ValueError('Output coverage/order/uniqueness failure')
    metrics=compare(sample,dataset.tables.get('sample_requests',[]))
    (out/'sample_metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    write_csv(out/'sample_predictions.csv',sample)
    (out/'image_extractions.json').write_text(json.dumps(images.audit,indent=2,ensure_ascii=False),encoding='utf-8')
    report={'requests_processed':len(final),'rows_generated':len(final),'samples_evaluated':len(sample),
        'coverage':1.0,'schema_columns':OUTPUT_COLUMNS,'validation_errors':all_errors,
        'requests_with_warnings':sum(bool(s['warnings']) for s in states),
        'requests_with_unresolved_obligations':sum(bool(s['blockers']) for s in states),
        'images_read':len(images.audit),'images_unresolved':sum(a['status']!='resolved' for a in images.audit),
        'status_counts':dict(Counter(r['affordability_status'] for r in final)),
        'method_counts':dict(Counter(r['recommended_payment_method'] for r in final)),
        'limitations':['Forecasts infer recurrence and conservative amounts from finite historical observations; not a guarantee of future actual balances.',
                       'Source examples conflict with the user deadline restriction for some earliest dates and spending-change full payments.',
                       'No hidden ground truth was available. Sample accuracy is measured without tuning rules to request identifiers.',
                       'Development-time visual extraction is replayed from content-addressed evidence; unseen images need local OCR or reviewed facts.']}
    (out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if all_errors:
        print(json.dumps({'validation_failed':all_errors[:10]},indent=2));return 2
    write_csv(args.output,final)
    if args.write_dataset_output and (args.dataset/'output.csv').resolve()!=args.output.resolve():
        write_csv(args.dataset/'output.csv',final)
    # Read back the artifact that will actually be submitted.
    with args.output.open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f);readback=list(reader)
        if reader.fieldnames!=OUTPUT_COLUMNS or readback!=final:raise ValueError('Output readback mismatch')
    report['output_sha256']=hashlib.sha256(args.output.read_bytes()).hexdigest()
    report['output_readback_valid']=True
    (out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    usage='''# Token usage and cost — final full-dataset run

The final run performs no external model/API calls. It uses deterministic Python,
Decimal arithmetic, local CSV/image reads, and SHA-256-verified document evidence.

| Provider/model | Calls | Input tokens | Output tokens | Total tokens | Estimated API cost |
|---|---:|---:|---:|---:|---:|
| None (deterministic replay) | 0 | 0 | 0 | 0 | USD 0 |

Requests: COUNT. Average runtime tokens per request: 0. Average API cost per request: USD 0.
Local compute/electricity costs are not measured or included.

Development used Codex for code and visual document inspection. Development input/output
tokens and attributable account cost are unavailable from this session and are NOT claimed
to be zero. Visual facts are cached document extractions, not predicted request labels.
The final run reopens and decodes all linked PNG files and verifies their hashes before reuse.
Unseen images optionally invoke local Tesseract OCR; no OCR invocation was needed in this run.
'''.replace('COUNT',str(len(final)))
    (out/'usage_report.md').write_text(usage,encoding='utf-8')
    print(json.dumps({'output':str(args.output.resolve()),'rows':len(final),'errors':len(all_errors),
                      'warnings':report['requests_with_warnings'],'sample_metrics':metrics['metrics']},indent=2))
    return 0

if __name__=='__main__':sys.exit(main())
