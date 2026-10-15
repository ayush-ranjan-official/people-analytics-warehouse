An analyst at the company has a question about its people data. The warehouse they query is
described in <schema> below, and their question is in <question>.

Write one BigQuery Standard SQL query that answers the question.

- It must be a single SELECT statement (WITH clauses are fine): no DML, DDL, scripting or
  temporary tables.
- Use only the tables in the schema, and name each one with its dataset, exactly as the schema
  does (for example `analytics.dim_employee`).
- Return the columns that answer the question, with short aliases. Don't round numbers.
- You can't ask a follow-up question. If the question is ambiguous, take the most reasonable
  reading and note it in a SQL comment. Return one answer: describe any other reading in a
  comment too, not as extra columns or rows.
- Reply with the query only, in one ```sql code block.
