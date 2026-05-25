# RAG-AWS Eval Results

- API: `https://qf915n6z4i.execute-api.us-east-1.amazonaws.com/prod`
- top_k: 5
- Questions: 8

| id | category | question | expected_doc | actual_top_source | score | confidence | answer | latency_ms |
|---|---|---|---|---|---:|---:|---|---:|
| q01 | in-corpus | What is the refund window for monthly plans? | refund-policy.md | refund-policy.md | 0.657 | 0.668 | According to the Acme Notes Refund Policy, full refunds are available for monthly plans within **30 days** of the most… | 3553 |
| q02 | in-corpus | How long does standard domestic shipping take for Acme Notebook orders in the U… | shipping-policy.md | shipping-policy.md | 0.864 | 0.840 | According to the Acme Notes Shipping Policy, standard domestic shipping for orders in the United States takes **3-5 bus… | 3963 |
| q03 | in-corpus | What authentication requirements does Acme Notes enforce for employee accounts? | security-policy.md | security-policy.md | 0.885 | 0.867 | # Authentication Requirements for Employee Accounts  Based on Acme Notes' Information Security Policy [1], the followin… | 5153 |
| q04 | in-corpus | How much paid time off do full-time Acme Notes employees accrue? | employee-handbook.md | employee-handbook.md | 0.885 | 0.858 | According to the Acme Notes Employee Handbook, full-time employees accrue paid time off (PTO) based on their tenure [1]… | 3868 |
| q05 | in-corpus | What are the differences between the Free, Pro, Team, and Enterprise plans? | product-faq.md | product-faq.md | 0.776 | 0.747 | # Differences Between Acme Notes Plans  [1]  - **Free**: 1 GB storage, up to 3 collaborators per workspace, 30 days of… | 4358 |
| q06 | in-corpus | What kinds of content are prohibited under the Acme Notes acceptable use policy? | acceptable-use.md | acceptable-use.md | 0.941 | 0.899 | # Prohibited Content Under Acme Notes AUP  According to the Acme Notes Acceptable Use Policy, customers may not upload,… | 4757 |
| q07 | ambiguous | What happens to my data if I cancel my account? | security-policy.md | acceptable-use.md | 0.577 | 0.200 | I don't have information about that in the knowledge base. | 3162 |
| q08 | off-corpus | What is the office WiFi password? | (off-corpus) | security-policy.md | 0.636 | 0.200 | I don't have information about that in the knowledge base. | 8102 |

## Aggregate metrics

- Source-match rate (in-corpus): **100.0%** (6/6)
- Mean confidence (in-corpus): **0.813**
- Mean confidence (off-corpus): **0.200**
- Total wall-clock: **38704 ms**
- Total API latency (sum): **36916 ms**
