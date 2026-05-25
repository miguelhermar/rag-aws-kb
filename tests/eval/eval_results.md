# RAG-AWS Eval Results

- API: `https://id04zftvx5.execute-api.us-east-1.amazonaws.com/prod`
- top_k: 5
- Questions: 8

| id | category | question | expected_doc | actual_top_source | score | confidence | answer | latency_ms |
|---|---|---|---|---|---:|---:|---|---:|
| q01 | in-corpus | What is the refund window for monthly plans? | refund-policy.md | refund-policy.md | 0.657 | 0.668 | According to the Acme Notes Refund Policy, full refunds for monthly plans are available within **30 days** of the most… | 1913 |
| q02 | in-corpus | How long does standard domestic shipping take for Acme Notebook orders in the U… | shipping-policy.md | shipping-policy.md | 0.864 | 0.840 | According to the Acme Notes Shipping Policy, standard domestic shipping in the United States takes **3-5 business days*… | 1716 |
| q03 | in-corpus | What authentication requirements does Acme Notes enforce for employee accounts? | security-policy.md | security-policy.md | 0.885 | 0.867 | # Authentication Requirements for Employee Accounts  Based on Acme Notes' Information Security Policy [1], the followin… | 3826 |
| q04 | in-corpus | How much paid time off do full-time Acme Notes employees accrue? | employee-handbook.md | employee-handbook.md | 0.885 | 0.858 | According to the Acme Notes Employee Handbook, full-time employees accrue the following paid time off based on tenure [… | 2204 |
| q05 | in-corpus | What are the differences between the Free, Pro, Team, and Enterprise plans? | product-faq.md | product-faq.md | 0.776 | 0.747 | # Acme Notes Plan Comparison  Based on the FAQ, here are the key differences between the four plans [1]:  \| Feature \|… | 3105 |
| q06 | in-corpus | What kinds of content are prohibited under the Acme Notes acceptable use policy? | acceptable-use.md | acceptable-use.md | 0.941 | 0.899 | According to the Acme Notes Acceptable Use Policy, the following kinds of content are prohibited [1]:  - **Illegal cont… | 2616 |
| q07 | ambiguous | What happens to my data if I cancel my account? | security-policy.md | acceptable-use.md | 0.577 | 0.200 | I don't have information about that in the knowledge base. | 1396 |
| q08 | off-corpus | What is the office WiFi password? | (off-corpus) | security-policy.md | 0.636 | 0.200 | I don't have information about that in the knowledge base. | 1596 |

## Aggregate metrics

- Source-match rate (in-corpus): **100.0%** (6/6)
- Mean confidence (in-corpus): **0.813**
- Mean confidence (off-corpus): **0.200**
- Total wall-clock: **20171 ms**
- Total API latency (sum): **18372 ms**
