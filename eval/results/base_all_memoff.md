# RefactorBench results — level=base, memory=off

- **In-scope pass rate:** 54.5% (6/11 in-scope tasks)
- **In-scope subtask completion:** 90.9% (80/88 subtests)
- **Out-of-scope (declined):** 89 of 100 tasks

| repo | task | kind | in-scope | subtests | pass |
|---|---|---|---|---|---|
| fastapi | get-auth-scheme-param | rename | yes | 6/6 | ✓ |
| fastapi | exception-handlers-to-handlers | module_rename | no | 0/12 |  |
| fastapi | add-log-parameter-generate-option-id-for-path | add_parameter | no | 0/5 |  |
| fastapi | value-is-a-sequence | rename | yes | 5/5 | ✓ |
| fastapi | params-to-param | module_rename | no | 0/22 |  |
| fastapi | openapi-get-utils | module_rename | no | 0/9 |  |
| celery | evaluate-promises-to-serialization | move | no | 0/4 |  |
| celery | dump-message-to-serialization | move | no | 0/3 |  |
| celery | autoretry-to-retry | module_rename | no | 0/7 |  |
| celery | add-log-parameter-node-format | add_parameter | no | 0/3 |  |
| celery | annotation-utils | move | no | 0/8 |  |
| celery | rename-host-format | module_rename | no | 0/7 |  |
| celery | expand-router-string-to-utils | move | no | 0/4 |  |
| celery | object-mro-lookup | import_fix | no | 0/3 |  |
| celery | truncate-text | module_rename | no | 0/9 |  |
| celery | ensure_serialize | rename | yes | 6/9 |  |
| celery | combine-unpickle | move | no | 0/6 |  |
| celery | add-log-parameter-get-digest-algorithm | add_parameter | no | 0/5 |  |
| scrapy | rename-description-commands | rename_unparsed | no | 0/7 |  |
| scrapy | new-verify-reactor-class | create | no | 0/11 |  |
| scrapy | add-log-parameter-job-dir | add_parameter | no | 0/5 |  |
| scrapy | parameterize-gunzip | encapsulate | no | 0/9 |  |
| scrapy | rename-processtest-testproc | module_rename | no | 0/6 |  |
| scrapy | genspider-functions-to-utils-url | move | no | 0/4 |  |
| scrapy | add-log-parameter-xmliter | add_parameter | no | 0/3 |  |
| scrapy | new-downloadermiddlewares-utils | move | no | 0/5 |  |
| scrapy | new-spider-utils-in-spiders | create | no | 0/8 |  |
| scrapy | add-log-parameter-disconnect-all | add_parameter | no | 0/6 |  |
| scrapy | sitemap-url-to-url | move | no | 0/3 |  |
| scrapy | rename-engine-status | other | no | 0/8 |  |
| scrapy | not-supported-exception-to-unsupported | other | no | 0/10 |  |
| ansible | sort-groups-to-group-sort | rename | yes | 4/4 | ✓ |
| ansible | combine-namespace-compat | combine | no | 0/7 |  |
| ansible | parse_key_value | rename | yes | 21/21 | ✓ |
| ansible | add-log-parameter-is-systemd-managed | add_parameter | no | 0/5 |  |
| ansible | new-inventory-patterns | move | no | 0/9 |  |
| ansible | data-to-inventory-data | module_rename | no | 0/5 |  |
| ansible | move-quoting-splitter | move | no | 0/6 |  |
| ansible | new-utils-class-connection | other | no | 0/7 |  |
| ansible | rename-lenient-lowercase | module_rename | no | 0/4 |  |
| ansible | new-utils-from-basic | move | no | 0/12 |  |
| ansible | add-log-parameter-get-group-vars | add_parameter | no | 0/5 |  |
| django | new-reference-context-field-class | other | no | 0/4 |  |
| django | new-path-traversal-exception | other | no | 0/9 |  |
| django | split-parse-apps-and-model-labels | other | no | 0/5 |  |
| django | remove-db-models-constants | move | no | 0/19 |  |
| django | add-log-parameter-resolve-error-handler | other | no | 0/3 |  |
| django | new-reference-context-graph-class | other | no | 0/9 |  |
| django | rename-file-move-safe | rename | yes | 4/6 |  |
| django | add-log-parameter-constant-time-compare | other | no | 0/3 |  |
| django | new-converter-to-python-class | other | no | 0/3 |  |
| django | remove-core-cache-utils | move | no | 0/5 |  |
| django | new-utils-path-from-module | move | no | 0/2 |  |
| django | new-utils-adapt-method-mode | other | no | 0/3 |  |
| django | combine-utils-dates-dateformat | other | no | 0/4 |  |
| django | add-none-handling-duration-string | other | no | 0/4 |  |
| django | combine-utils-hashable-itercompat | move | no | 0/4 |  |
| django | new-timezone-class | other | no | 0/4 |  |
| django | add-log-parameter-get-resolver | other | no | 0/3 |  |
| django | new-utils-check-response | move | no | 0/2 |  |
| salt | channel-to-transport | module_rename | no | 0/6 |  |
| salt | namecheap-xmlutil | move | no | 0/6 |  |
| salt | cant-create | rename_unparsed | no | 0/8 |  |
| salt | add-log-parameter-get-capability-definitions | add_parameter | no | 0/3 |  |
| salt | ex-state-fail | rename | yes | 6/6 | ✓ |
| salt | perm-denied | rename_unparsed | no | 0/5 |  |
| salt | iam-to-aws | move | no | 0/5 |  |
| salt | exactly-n-boto-mod | other | no | 0/4 |  |
| salt | pem-fingerprint | rename | yes | 9/10 |  |
| salt | get-unavail | rename_unparsed | no | 0/10 |  |
| salt | add-log-parameter-delete-directory | add_parameter | no | 0/4 |  |
| salt | mksls-to-specific | rename_unparsed | no | 0/6 |  |
| salt | add-log-parameter-recursive-diff | add_parameter | no | 0/6 |  |
| salt | ex-pillar-fail | rename | yes | 5/5 | ✓ |
| salt | paged-call-boto-mod | other | no | 0/4 |  |
| flask | rename-send-from-directory | rename_unparsed | no | 0/4 |  |
| flask | debughelpers-to-helpers.py | move | no | 0/5 |  |
| flask | add-log-parameter-get-debug-flag | add_parameter | no | 0/7 |  |
| flask | render-template-str | rename | yes | 9/10 |  |
| flask | add-log-parameter-get-flashed-messages | add_parameter | no | 0/3 |  |
| flask | stream-template-str | rename_unparsed | no | 0/4 |  |
| requests | add-log-parameter-get-encoding-from-headers | add_parameter | no | 0/3 |  |
| requests | add-log-parameter-select-proxy | add_parameter | no | 0/2 |  |
| requests | rename-lookup-dict-dict-lookup | module_rename | no | 0/7 |  |
| requests | add-log-parameter-resolve-proxies | add_parameter | no | 0/2 |  |
| requests | combine-internal-utils-utils | move | no | 0/7 |  |
| requests | new-cookie-utils-class | create | no | 0/4 |  |
| requests | move-hooks-sessions | move | no | 0/7 |  |
| requests | combine-from-key-to-key | other | no | 0/5 |  |
| requests | rename-super-len-complex-len | rename_unparsed | no | 0/3 |  |
| requests | split-warnings-exceptions | move | no | 0/9 |  |
| tornado | global-objects | move | no | 0/4 |  |
| tornado | log-utils | move | no | 0/6 |  |
| tornado | option-parser-with-pretty-print | other | no | 0/5 |  |
| tornado | options-utils | move | no | 0/23 |  |
| tornado | remove-locale-data | move | no | 0/3 |  |
| tornado | rename-http1connection | rename | yes | 5/6 |  |
| tornado | rename-to-camel-case | rename_unparsed | no | 0/4 |  |
| tornado | resolvers-as-separate | move | no | 0/18 |  |
| tornado | tcpclient-connect-params | other | no | 0/6 |  |
