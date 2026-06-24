# mannual_src — manual oracle-debug harness

One script per UNVERIFIED bug (52 total: 25 fix_only + 27 buggy_only).
Each script: (1) runs the oracle check (fix should PASS, buggy should FAIL),
(2) drops you into the docker container at the bug's working dir to debug by hand.

## Categories
- **FIX_ONLY (fo)**: buggy commit wrongly PASSES — the test cannot catch the bug (too lax / no ASAN / missing crash-detection). Goal: make buggy FAIL.
- **BUGGY_ONLY (bo)**: fix commit wrongly FAILS — its own build/config/test is broken. Goal: make fix PASS.

## Run
```bash
bash mannual_src/<proj>@<sha>.sh          # oracle check + drop into container
bash mannual_src/<proj>@<sha>.sh check    # just the oracle check
bash mannual_src/<proj>@<sha>.sh shell    # straight into the container to debug
```
Prereq: webapp up on :8095 (docker compose up -d in defects4c_docker_web4).

## Bugs by project

| project | FO (buggy wrongly passes) | BO (fix wrongly fails) |
|---|---|---|
| KhronosGroup___SPIRV-Tools | 5 | 6 |
| nginx___njs | 3 | 7 |
| php___php-src | 3 | 5 |
| the-tcpdump-group___tcpdump | 6 | 1 |
| libgd___libgd | 3 | 0 |
| facebook___rocksdb | 1 | 1 |
| libuv___libuv | 2 | 0 |
| sqlite___sqlite | 1 | 1 |
| znc___znc | 0 | 2 |
| DynamoRIO___dynamorio | 0 | 1 |
| jqlang___jq | 1 | 0 |
| mdadams___jasper | 0 | 1 |
| nanomsg___nng | 0 | 1 |
| redis___redis | 0 | 1 |

## All scripts

- `DynamoRIO___dynamorio@452c17abfd19.sh` — **BO** — src=`core/ir/x86/decode.c` cve=Logic Organization: Improper Condition Organization
- `KhronosGroup___SPIRV-Tools@0a5d99d02cb8.sh` — **BO** — src=`source/opt/optimizer.cpp` cve=Signature: Incorrect Function Return Value
- `KhronosGroup___SPIRV-Tools@0ad83f9139da.sh` — **BO** — src=`source/diff/diff.cpp` cve=Logic Organization: Wrong Function Call Sequence
- `KhronosGroup___SPIRV-Tools@4fa1a6f9b497.sh` — **BO** — src=`source/opt/ccp_pass.cpp` cve=Signature: Incorrect Function Usage
- `KhronosGroup___SPIRV-Tools@54385458ca2f.sh` — **BO** — src=`source/opt/register_pressure.cpp` cve=Logic Organization: Improper Condition Organization
- `KhronosGroup___SPIRV-Tools@948577c5df3a.sh` — **BO** — src=`source/opt/folding_rules.cpp` cve=Sanitizer: Control Expression Error
- `KhronosGroup___SPIRV-Tools@ab3cdcaef56e.sh` — **BO** — src=`source/opt/upgrade_memory_model.cpp` cve=Memory Error: Memory Overflow
- `facebook___rocksdb@cc8ded6152c5.sh` — **BO** — src=`db/compaction/compaction_iterator.cc` cve=Signature: Incorrect Function Return Value
- `mdadams___jasper@f25486c3d4aa.sh` — **BO** — src=`src/libjasper/jpc/jpc_t2cod.c` cve=CVE-2016-9583
- `nanomsg___nng@111b241473ce.sh` — **BO** — src=`src/core/message.c` cve=Memory Error: Uncontrolled Resource Consumption
- `nginx___njs@222d6fdcf0c6.sh` — **BO** — src=`src/njs_vmcode.c` cve=CVE-2022-29369
- `nginx___njs@2e00e9547386.sh` — **BO** — src=`src/njs_array.c` cve=CVE-2022-29779
- `nginx___njs@5c6130a2a0b4.sh` — **BO** — src=`src/njs_typed_array.c` cve=CVE-2022-30503
- `nginx___njs@ab1702c7af99.sh` — **BO** — src=`src/njs_module.c` cve=CVE-2022-29379
- `nginx___njs@ad48705bf1f0.sh` — **BO** — src=`src/njs_function.c` cve=CVE-2022-27007
- `nginx___njs@d457c9545e7e.sh` — **BO** — src=`src/njs_vmcode.c` cve=CVE-2021-46461
- `nginx___njs@eafe4c7a326b.sh` — **BO** — src=`src/njs_iterator.c` cve=CVE-2022-31307
- `php___php-src@1bd103df00f4.sh` — **BO** — src=`ext/gd/gd.c` cve=CVE-2016-7127
- `php___php-src@28a6ed9f9a36.sh` — **BO** — src=`ext/spl/spl_dllist.c` cve=CVE-2016-3132
- `php___php-src@426aeb280895.sh` — **BO** — src=`ext/wddx/wddx.c` cve=CVE-2016-7129
- `php___php-src@698a691724c0.sh` — **BO** — src=`ext/wddx/wddx.c` cve=CVE-2016-7130
- `php___php-src@ca46d0acbce5.sh` — **BO** — src=`ext/phar/phar.c` cve=CVE-2016-10159
- `redis___redis@bc7fe41e5857.sh` — **BO** — src=`src/t_hash.c` cve=CVE-2023-28856
- `sqlite___sqlite@e59c562b3f68.sh` — **BO** — src=`src/select.c` cve=CVE-2019-19244
- `the-tcpdump-group___tcpdump@ffde45acf334.sh` — **BO** — src=`print-bgp.c` cve=CVE-2017-12994
- `znc___znc@2390ad111bde.sh` — **BO** — src=`src/Client.cpp` cve=CVE-2020-13775
- `znc___znc@d229761821da.sh` — **BO** — src=`src/Client.cpp` cve=CVE-2020-13775
- `KhronosGroup___SPIRV-Tools@0391d0823ebf.sh` — **FO** — src=`source/opt/value_number_table.cpp` cve=Sanitizer: Control Expression Error
- `KhronosGroup___SPIRV-Tools@0a43a84e0224.sh` — **FO** — src=`source/opt/folding_rules.cpp` cve=Sanitizer: Control Expression Error
- `KhronosGroup___SPIRV-Tools@286b3095dd18.sh` — **FO** — src=`source/opt/ccp_pass.cpp` cve=Sanitizer: Control Expression Error
- `KhronosGroup___SPIRV-Tools@6a9be627c760.sh` — **FO** — src=`source/opt/optimizer.cpp` cve=Logic Organization: Wrong Function Call Sequence
- `KhronosGroup___SPIRV-Tools@d5a3bfcf2ffd.sh` — **FO** — src=`source/opt/instruction.cpp` cve=Logic Organization: Improper Condition Organization
- `facebook___rocksdb@b87c355772c0.sh` — **FO** — src=`db/db_iter.cc` cve=Sanitizer: Control Expression Error
- `jqlang___jq@c9a51565214e.sh` — **FO** — src=`src/jv.c` cve=CVE-2023-50268
- `libgd___libgd@1846f48e5fcd.sh` — **FO** — src=`src/gd.c` cve=CVE-2016-9317
- `libgd___libgd@58b6dde319c3.sh` — **FO** — src=`src/gd_tga.c` cve=CVE-2016-6906
- `libgd___libgd@69d2fd2c597f.sh` — **FO** — src=`src/gd_gd2.c` cve=CVE-2016-10168
- `libuv___libuv@0f2d7e784a25.sh` — **FO** — src=`src/idna.c` cve=CVE-2024-24806
- `libuv___libuv@3530bcc30350.sh` — **FO** — src=`src/idna.c` cve=CVE-2024-24806
- `nginx___njs@81af26364c21.sh` — **FO** — src=`src/njs_array.c` cve=CVE-2022-31306
- `nginx___njs@8b39afdad9a0.sh` — **FO** — src=`src/njs_array.c` cve=CVE-2022-29780
- `nginx___njs@f65981b0b8fc.sh` — **FO** — src=`src/njs_vmcode.c` cve=CVE-2022-28049
- `php___php-src@523f230c831d.sh` — **FO** — src=`ext/standard/http_fopen_wrapper.c` cve=CVE-2018-7584
- `php___php-src@b2af4e886872.sh` — **FO** — src=`ext/standard/var.c` cve=CVE-2016-9936
- `php___php-src@b88393f08a55.sh` — **FO** — src=`ext/wddx/wddx.c` cve=CVE-2016-7413
- `sqlite___sqlite@a6c1a71cde08.sh` — **FO** — src=`src/select.c` cve=CVE-2019-20218
- `the-tcpdump-group___tcpdump@1bc78d795cd5.sh` — **FO** — src=`print-radius.c` cve=CVE-2017-13032
- `the-tcpdump-group___tcpdump@3c8a2b0e91d8.sh` — **FO** — src=`print-rsvp.c` cve=CVE-2017-13048
- `the-tcpdump-group___tcpdump@5edf405d7ed9.sh` — **FO** — src=`print-802_11.c` cve=CVE-2017-13008
- `the-tcpdump-group___tcpdump@979dcefd7b25.sh` — **FO** — src=`print-isoclns.c` cve=CVE-2017-12998
- `the-tcpdump-group___tcpdump@d10a0f980fe8.sh` — **FO** — src=`print-bgp.c` cve=CVE-2017-13046
- `the-tcpdump-group___tcpdump@da6f1a677bfa.sh` — **FO** — src=`print-pgm.c` cve=CVE-2017-13034
