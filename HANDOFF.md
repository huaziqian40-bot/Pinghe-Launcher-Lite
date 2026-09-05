# Hello! Pinghe launcher 鈥?椤圭洰浜ゆ帴鏂囨。

> 鍐欑粰鎺ユ墜鐨勬柊 agent銆傝繖閲屽寘鍚」鐩殑鍏ㄩ儴鑳屾櫙銆佹灦鏋勩€佽俯杩囩殑鍧戝拰褰撳墠鐘舵€併€?> 璇诲畬鍚庝綘搴旇鑳界嫭绔嬬户缁紑鍙戝拰缁存姢杩欎釜椤圭洰銆?
---

## 涓€銆侀」鐩槸浠€涔?
**Hello! Pinghe launcher**(鍘熷悕 SchoolHub)鏄粰涓婃捣骞冲拰瀛︽牎瀛︾敓鐢ㄧ殑鏈湴瀛︿範鍔╂墜:

- **鏁版嵁婧?*:Edupage(璇捐〃/鑰冨嫟)銆丮anageBac(IB 璇剧▼浣滀笟/鎴愮哗/DDL)銆佺綉鏄撲紒涓氶偖绠?閭欢/閫氳褰?
- **鏍稿績鍗栫偣**:鏈湴杩愯銆佹暟鎹笉鍑烘満鍣?AI 鍔╂墜鍙互鏌ヨ琛?DDL/閭欢/鑱旂郴浜恒€佽捣鑽?Word 浣滀笟銆佷唬鍙戦偖浠躲€佷唬浜や綔涓?鍏ㄩ儴瑕佺敤鎴风‘璁?
- **鎶€鏈爤**:Python 3.14 + pywebview(EdgeChromium/WebView2) + requests + edupage-api + anthropic/openai SDK + PyInstaller + WiX 3.14.1
- **鐢ㄦ埛**:骞冲拰瀛︽牎 IB 椤圭洰瀛︾敓(褰撳墠娴嬭瘯璐﹀彿:IB grade 11 class 9 / 涔濈彮)

## 浜屻€佺洰褰曚笌鐜

### 寮€鍙戠洰褰?
- **`D:\HPHL-dev\`** 鈥?鍞竴鐨勫紑鍙戜粨搴?git repo,鍒嗘敮 master)
  - `hellopinghe/` 鈥?Python 鍖?鏍稿績浠ｇ爜)
  - `ui/` 鈥?鍓嶇(app.js / index.html / styles.css / logo.png)
  - `installer/` 鈥?WiX 瀹氫箟 + 浜х墿 HelloPingheLauncher.msi
  - `tools/wix314/` 鈥?WiX 3.14.1 渚挎惡鐗?candle.exe / light.exe)
  - `HelloPingheLauncher.spec` 鈥?PyInstaller 鎵撳寘閰嶇疆(鍐呭祵 `icon='logo.ico'` + `--add-data "ui;ui"`)
  - `run_hellopinghe.py` 鈥?婧愮爜鍚姩鍏ュ彛
  - `HelloPingheLauncher.exe` 鈥?缁胯壊鐗?浠撳簱鏍?宸茶窡韪繘 git)
  - `logo.ico` / `logo.png` 鈥?鍥炬爣(婧愬浘鍦?`D:\HPHL\logo.png`)
  - `_ui_test.py` / `_ui_dbg_week.py` 鈥?UI 娴嬭瘯鑴氭湰(gitignored)

**鈿?鐢ㄦ埛鏄庣‘瑕佹眰:鎵€鏈変慨鏀瑰彧鍦?`D:\HPHL-dev` 鍋?涓嶈鍔ㄦ湰鏈哄叾浠栭」鐩殑鏁版嵁銆?*

### 鐢ㄦ埛鏁版嵁鐩綍(涓嶅湪浠撳簱閲?瑁呭湪鐢ㄦ埛瀹剁洰褰?

- `~/.hellopinghe/` 鈥?config.json(璐﹀彿/AI provider)銆乭ellopinghe.db(SQLite:浣滀笟缂撳瓨/鏃ョ▼/ dismissed DDL)銆乤gent_sessions/(Agent 浼氳瘽)銆乪dupage_week_v3_*.json(鏁村懆璇捐〃缂撳瓨 6h)銆乪dupage_personal_v5_{day}_{selhash}.json(涓汉璇捐〃缂撳瓨 2h)銆乵ail_contacts.json(閫氳褰?24h)銆乧ontacts_custom.json(鐢ㄦ埛鑷缓鑱旂郴浜?闅愯棌澧撶)銆乻ession_{host}.json(ManageBac 浼氳瘽 cookie)
- Windows 鍑嵁绠＄悊鍣?keyring 鏈嶅姟鍚?`hellopinghe`):
  - `edupage:{subdomain}:{username}` 鈥?Edupage 瀵嗙爜
  - `mail:{email}` 鈥?閭缃戦〉瀵嗙爜
  - `mail_authcode:{email}` 鈥?閭瀹㈡埛绔巿鏉冪爜
  - `managebac:{base_url}` 鈥?ManageBac 瀵嗙爜

**鈿?棣栨瀵煎叆**:`config.py::_migrate_legacy()` 浼氬湪妯″潡瀵煎叆鏃惰嚜鍔ㄦ妸鏃х洰褰?`~/.schoolhub` 鐨勬暟鎹拰鏃?keyring 鏈嶅姟 `schoolhub` 鐨勫瘑閽ヨ縼鍒版柊浣嶇疆(骞傜瓑,闈欓粯澶辫触)銆?
### 鏋勫缓鍛戒护

```bash
# exe(浜у嚭 dist/HelloPingheLauncher.exe,璁板緱 cp 鍒颁粨搴撴牴)
cd D:\HPHL-dev
python -m PyInstaller --noconfirm --clean HelloPingheLauncher.spec
cp -f dist/HelloPingheLauncher.exe ./HelloPingheLauncher.exe

# MSI(鈿?涓嶈鍔?-ext WixUIExtension,浼氭妸鏁版嵁搴撲唬鐮侀〉鍘嬪洖 1252 瀵艰嚧涓枃 LGHT0311)
cd installer
..\tools\wix314\candle.exe HelloPingheLauncher.wxs -nologo
..\tools\wix314\light.exe HelloPingheLauncher.wixobj -out HelloPingheLauncher.msi -nologo
rm -f HelloPingheLauncher.wixobj HelloPingheLauncher.wixpdb   # 娓呯悊涓棿浜х墿

# 鎵撳寘鍓嶉殣绉佹壂鎻?蹇呴』闆跺懡涓?: REDACTED-ACCOUNT / REDACTED-PASSWORD / REDACTED-PASSWORD / REDACTED-AUTHCODE / REDACTED-KEY
grep -rilE "REDACTED-ACCOUNT|REDACTED-PASSWORD|REDACTED-AUTHCODE|REDACTED-KEY" --include="*.py" --include="*.js" --include="*.html" --include="*.css" --include="*.wxs" --exclude-dir=.git .
```

## 涓夈€佷唬鐮佹灦鏋?
```
hellopinghe/
鈹溾攢鈹€ config.py          # Config dataclass + JSON 璇诲啓 + _migrate_legacy()
鈹溾攢鈹€ exceptions.py      # PingheError / LoginRequiredError / LoginError
鈹溾攢鈹€ storage.py         # SQLite(浣滀笟缂撳瓨/鏃ョ▼/dismissed DDL/閫氳褰曚笉鍦ㄨ繖)
鈹溾攢鈹€ managebac/
鈹?  鈹溾攢鈹€ client.py      # ManageBacClient: 绾?HTTP 鐧诲綍/鏁版嵁鎶撳彇
鈹?  鈹斺攢鈹€ parse.py       # HTML 瑙ｆ瀽(浣滀笟鍗?DDL/鎴愮哗/璇剧▼鍒楄〃)
鈹溾攢鈹€ app/
鈹?  鈹溾攢鈹€ __main__.py    # 绐楀彛鍒涘缓 + --smoke 娴嬭瘯鍏ュ彛
鈹?  鈹溾攢鈹€ bridge.py      # js_api 妗ユ帴灞?Api 绫? 鍏ㄩ儴鏂规硶杩斿洖 {ok, data|error})
鈹?  鈹溾攢鈹€ services.py    # 涓氬姟鏈嶅姟灞?Edupage/FreeRooms/Mail/Schedule/Courses)
鈹?  鈹溾攢鈹€ agent.py       # Agent 寮曟搸(宸ュ叿寰幆 + 鎻愭纭鏈哄埗)
鈹?  鈹斺攢鈹€ ...
鈹斺攢鈹€ cli.py             # 鍛戒护琛屽叆鍙?
ui/
鈹溾攢鈹€ index.html         # 鍗曢〉 UI(8 涓鍥? home/timetable/schedule/gradett/courses/mail/agent/settings)
鈹溾攢鈹€ app.js             # 鍏ㄩ儴鍓嶇閫昏緫(~1900 琛?
鈹溾攢鈹€ styles.css         # 鏍峰紡(~400 琛? 璁捐 token 娌跨敤 PH-Launcher 澧ㄧ豢/閲?璞＄墮鐧?
鈹斺攢鈹€ logo.png           # 宸︿笂瑙?logo(婧愬浘 D:\HPHL\logo.png, 榛戝簳鍍忕礌椋?

ui 浜や簰澶囧繕(2026-09-05):
- 璇捐〃鏈?褰撳墠鏃堕棿"閲戠嚎(#tt-nowline, updateNowLine(), 30s 鍒锋柊, 浠呮湰鍛ㄦ樉绀?
  娓叉煋鍚庨噸鎸?鈥斺€?鏀?renderTimetable 鏃跺埆涓㈡帀鏈熬閭ｆ updateNowLine() 璋冪敤)
- 渚ф爮 logo(#logo)鐐瑰嚮 = 鍥為椤?- 棣栭〉"姝ｅ湪涓婄殑璇?鍗￠噷鏈?涓嬩竴鑺?(home_data.next_lesson, 浠婂ぉ鍓╀綑鏈€杩戜竴鑺?
- 璇剧▼ chip 鏈?鈻测柤 绠ご鎺掑簭, 涓庢寚閽堟嫋鎷藉叡鐢?saveChipOrder; 绠ご鐐瑰嚮
  pointerdown 琚?bindChipDrag 鎺掗櫎, 涓嶄細瑙﹀彂 chip 鐨勭偣鍑荤瓫閫?```

### 鍏抽敭鏁版嵁娴?
```
ui/app.js (SWR缂撳瓨 localStorage "sh_*")
    鈫?window.pywebview.api.<鏂规硶>()
bridge.py Api 绫?(_SNAP 杩涚▼鍐?TTL 蹇収 + _wrap 缁熶竴閿欒)
    鈫?services.py (EdupageService / ManageBacClient / MailService / ScheduleService / AgentEngine)
    鈫?Edupage / ManageBac / 缃戞槗IMAP路SMTP / SQLite / keyring / 鏂囦欢绯荤粺
```

### 璇捐〃鏁版嵁绠＄嚎(鏈€澶嶆潅鐨勪竴鍧?

1. `EdupageService._ensure()` 鈥?鐧诲綍(甯?`_patch` 瓒呮椂 + `_speed_patch` 鎬ц兘琛ヤ竵,瑙佷笅)
2. `week_plans(monday, days)` 鈥?3 涓?gcall 绐楀彛(閿氱偣鍛ㄤ竴/鍛ㄥ洓/鍛ㄦ棩)鍚堝苟 鈫?`_parse_week()` 瑙ｆ瀽鎴?`{day_iso: [Lesson...]}`;纾佺洏缂撳瓨 `edupage_week_v3_{monday}.json`(6h)
3. `personal(day)` 鈥?浠?master_plan 鎸夐€夎杩囨护;纾佺洏缂撳瓨 `edupage_personal_v5_{day}_{selhash}.json`(2h)
4. `master_plan(day)` 鈥?鍏堟煡 week_plans,缂鸿繖澶╁洖閫€ `ed.get_my_timetable(day)`(鎱?浣嗗凡琚?speed_patch 鍔犻€?

## 鍥涖€佽俯杩囩殑鍧?閲嶈!)

### Edupage 鏈嶅姟绔涓?
1. **gcall loadData 蹇界暐 dateto** 鈥?鍙繑鍥炰互 `date` 涓轰腑蹇冪殑 3 澶╃獥鍙?鍓嶄竴澶?褰撳ぉ+鍚庝竴澶?銆傚疄娴?璇锋眰鍛ㄤ竴鈫掕繑鍥炲懆鏃鍛ㄤ簩銆傛墍浠ュ繀椤诲垎 3 涓敋鐐?鍛ㄤ竴/鍛ㄥ洓/鍛ㄦ棩)鍚勬媺涓€娆″啀鍚堝苟,鍚﹀垯鍛ㄤ笁~鍛ㄤ簲涓㈠け
2. **鍛ㄦ棩閿氱偣绐楀彛浼氭硠婕忎笅鍛ㄥ懆涓€** 鈥?鍚堝苟鍚庡繀椤昏鍓埌璇锋眰鍖洪棿 `[monday, monday+days)`,鍚﹀垯绉戠洰鏍囬€夐」閲屼細鍑虹幇"涓嬪懆鐨勫懆涓€"閫犳垚鏃堕棿閲嶅
3. **address 澶存槸 IMAP ENVELOPE 搴忓垪鍖栨牸寮?* 鈥?`BODY[HEADER.FIELDS (FROM TO CC)]` 杩斿洖 `(("鍚? NIL "local" "domain"))` 鑰岄潪 RFC5322,`email.utils.getaddresses` 瑙ｆ瀽涓嶄簡,闇€瑕佽嚜鍐欐嫭鍙峰垎璇嶅櫒(`_envelope_addresses`)
4. **鏂囦欢澶瑰悕鏄?modified UTF-7** 鈥?`&XfJT0ZAB-` = 宸插彂閫?`&g0l6P3ux-` = 鑽夌绠便€俙email.header.decode_header` 瑙ｄ笉浜?闇€瑕?`_mutf7_decode`
5. **UID SEARCH 甯?CHARSET 浼氳繑鍥?BAD** 鈥?瑁?imaplib 鍛戒护涓嶈鍔?CHARSET 鍙傛暟

### edupage-api 鎬ц兘鐥呯悊(濡傛灉涓嶄慨,鏁村懆瑙ｆ瀽 60 绉?)

`get_teachers/get_classes/get_subjects/get_classrooms` 姣忔璋冪敤閮介噸鏂拌В鏋愭暣涓?dbi 鍒楄〃,鑰岃琛ㄨВ鏋愭瘡寮犺鍗￠兘瑕佹煡涓€娆?鈫?O(鍗＄墖鏁?脳 鍏ㄨ〃瑙ｆ瀽)銆傚疄娴?149 寮犲崱 = 9.6 涓囨 get_teacher銆?400 涓囨瀵硅薄瑙ｆ瀽銆?**淇**:`EdupageService._speed_patch(ed)` 鎶婅繖 4 涓?鍏ㄩ噺鍒楄〃"鏂规硶鎸?Edupage 瀹炰緥缂撳瓨(helper 瀵硅薄姣忓紶鍗℃柊寤轰竴涓?鎵€浠ュ繀椤绘寕鍦?edupage 瀹炰緥涓婅€屼笉鏄?helper 涓?銆備慨澶嶅悗鏁村懆瑙ｆ瀽 63s鈫?.02s銆?
### ManageBac 鎻愪氦(2026-09-05 瀹炴祴瀹氳)

- **鐪熷疄璺敱/瀛楁(鍙鎺㈡祴楠岃瘉)**: shph 鐨勬彁浜?action 鏄?  `/student/classes/<cid>/core_tasks/<tid>/dropbox/upload`, 鏂囦欢瀛楁鍚嶆槸
  `dropbox[assets_attributes][0][file]`(鏂规嫭鍙烽鏍? 鈥斺€?纭紪鐮佹棫璺敱
  `.../dropbox` 鎴栨棫瀛楁 `dropbox_assets_attributes_0_file` 閮戒細 404/澶辫触銆?  `submit_task()` 鍥犳**鎵撳紑浠诲姟椤靛姩鎬佽В鏋愭彁浜ゅ叆鍙?*(鍏堟壘椤甸潰涓婂甫
  dropbox action 鐨?file 琛ㄥ崟, 鍐嶆壘甯︿换鍔?id 鐨?dropbox 閾炬帴鎵撳紑瀛愰〉闈?,
  authenticity_token 浠庤〃鍗?`meta csrf-token` 鍙栥€?- **杩囨湡 id 鍏滃簳**: agent 鍙兘鎷垮埌涓婂鏈熺殑杩囨湡 id(瀹炴祴 39792/88547 宸?  娑堝け, 杩?`/student/classes/39792` 閮?404)銆備换鍔￠〉鎵撲笉寮€鏃? 鎵綋鍓嶅叏閮?  璇剧▼ core_tasks 鎸?task_id 閲嶅畾浣嶇湡瀹?href; 杩樻壘涓嶅埌灏辨姤
  銆屼换鍔″彲鑳藉凡琚垹闄?褰掓。, 璇峰埌 ManageBac 缃戦〉纭銆嶈€屼笉鏄８ HTTP 閿欒銆?- 鎺㈡祴鑴氭湰 `_probe_dropbox.py` / `_probe_locate.py`(gitignored)鏄彧璇荤殑
  (缁濅笉 POST/涓嶇湡瀹炴彁浜?, 瀛︽牎鏀圭増鏃跺彲閲嶈窇鐪嬫柊璺敱銆?
### 鏋勫缓闄烽槺

- **WiX light 鍗冧竾涓嶈鍔?`-ext WixUIExtension`** 鈥?瀹冨唴宓岀殑 en-US .wxl 浼氭妸鏁版嵁搴撲唬鐮侀〉寮哄埗鍥?1252,涓枃鍐呭鐩存帴 LGHT0311 澶辫触(鍗充娇 Product/@Codepage="936")
- WiX 涓棿浜х墿 .wixobj/.wixpdb/.msi 鐢ㄥ畬瑕佹竻鐞?涓嶈鎻愪氦杩?git
- PyInstaller onefile 鐨?exe 鏄帇缂╃殑,**瀵?exe 鍋氫簩杩涘埗瀛楃涓叉壂鎻忔壘涓嶅埌浠讳綍涓滆タ**(鍖呮嫭鏁忔劅淇℃伅),闅愮鎵弿瑕佸湪婧愮爜灞傚仛
- exe 鏋勫缓鍚庡繀椤?`cp dist/HelloPingheLauncher.exe .` 鍚屾鍒颁粨搴撴牴(鐢ㄦ埛浼氱湅鏍圭洰褰曢偅涓?

### 娴嬭瘯鍩虹璁炬柦

- 鍐掔儫:`python -X utf8 -m hellopinghe.app --smoke` 鈫?鏈熸湜 `SMOKE_JS: dom-ok|js-ok`
- UI 浜や簰娴嬭瘯:`_ui_test.py`(gitignored)鈥?鐪熷疄 pywebview 绐楀彛 + MockApi(涓嶈繛鐪熷疄鏈嶅姟/涓嶇鐪熷疄涓汉鏁版嵁),evaluate_js 椹卞姩鐐瑰嚮/鎷栨嫿/鎸囬拡婊戝姩,鏂█鍐欒繘 `window.__ui_result`
- **鏀瑰墠绔氦浜掑悗蹇呴』璺?UI 娴嬭瘯,涓嶈兘鍙窇 smoke**(smoke 鍙獙璇?DOM+JS 璇硶,涓嶉獙璇佷氦浜掗€昏緫)

### 缂撳瓨澶辨晥(鍙屽眰)

- 鍚庣 `_SNAP` 蹇収(TTL: home 60s / tt 120s / courses 180s / gt 300s / mail 45s / contacts 600s)
- 鍓嶇 localStorage SWR(Store,閿墠缂€ `sh_`)
- **閫夎鍙樻洿蹇呴』鍚屾椂澶辨晥涓ゅ眰**:`wizard_save_selection` 宸?drop 鍚庣 `tt|*` + `home`;鍓嶇 `sm-save` 宸?drop `tt|` + `home`銆傛紡鎺変换浣曚竴灞?= 鐢ㄦ埛鏀瑰畬閫夎鐪嬪埌鏃ф暟鎹?- 閭欢鍙戦€?鈫?drop `mail|` + `home`;璇剧▼鍚屾(refresh_tasks)鈫?drop `courses` + `home`;DDL 宸︽粦绉婚櫎 鈫?drop `home` + `courses`

### 娌欑/宸ヤ綔鍖?
- DSH 鏂囦欢娌欑宸ヤ綔鍖烘槸 `D:\HPHL`,鑰屽紑鍙戠洰褰曟槸 `D:\HPHL-dev`鈥斺€攕hell 鍐欐枃浠跺埌 D:\HPHL-dev 鍙兘琚嫤(绛栫暐鍙樹簡),鐢?write/edit 宸ュ叿鍐欍€佹垨璁╃敤鎴锋妸 D:\HPHL-dev 鍔犺繘娌欑鐧藉悕鍗?- taskkill 鍦?git-bash 閲?`//IM` 鍙傛暟浼氳杞箟閿?鐢?PowerShell `Stop-Process` 浠ｆ浛
- msiexec 鍙傛暟涔熶細琚?git-bash 杞箟,鐢?PowerShell `Start-Process msiexec -ArgumentList '/i',...` 椹卞姩

## 浜斻€丄gent 宸ュ叿娓呭崟(build_tools)

| 宸ュ叿 | 鍔熻兘 | 绫诲瀷 |
|---|---|---|
| get_timetable | 鏈潵 N 澶╀釜浜鸿琛?| 鍙 |
| get_ddl | 鏈潵 N 澶?ManageBac DDL | 鍙 |
| get_class_tasks | 鍗曢棬/鍏ㄩ儴璇句綔涓氬崱(鍚凡鎴) | 鍙 |
| get_grades | 鍚勭鎬昏瘎(闇€鐢ㄦ埛鍏佽) | 鍙 |
| list_mail / read_mail | 閭欢鍒楄〃/姝ｆ枃 | 鍙 |
| get_schedule | 鏈湴鏃ョ▼(鍖洪棿) | 鍙 |
| search_contacts | 閫氳褰曟寜鍚嶅瓧/閭鎼滆仈绯讳汉 | 鍙 |
| list_workspace / read_docx / read_text_file | workspace 鏂囦欢鎿嶄綔 | 鍙 |
| create_docx / append_to_docx | 鎻愭:鍐?Word | 鍐?|
| add_schedule_event | 鎻愭:鏂板鏃ョ▼ | 鍐?|
| send_email | 鎻愭:鍙戦偖浠?鍏?search_contacts 鏌ラ偖绠? | 鍐?|
| submit_managebac_task | 鎻愭:浜や綔涓?| 鍐?|

鎵€鏈夊啓鎿嶄綔璧?`_propose` 鈫?鐢ㄦ埛纭 鈫?`agent_confirm(pid)` 鎵嶆墽琛屻€?
## 鍏€佽绋嬫ā鍨嬬粏鑺?
- **浣滄伅**(ui/app.js `PERIODS`):P1 8:00-8:40 / P2 8:45-9:25 / P3 9:35-10:15 / P4 10:20-11:00 / P5 11:05-11:55 / Lunch 12:00-12:40 / P6 12:45-13:25 / P7 13:30-14:10 / P8 14:15-14:55 / P9 15:00-15:40 / P10 15:45-16:25 / 鏅氳嚜涔?18:00-20:30
- **鍛ㄤ簲 12:45 璧锋槸璧扮彮杞崲璇?*:璇惧悕甯﹁疆娆″彿(HL1鈫扝L2)銆佽€佸笀浼氭崲銆傞€夎鍣ㄦ寜鏁欏缁?绉戠洰鏃?缁勫彿+鑰佸笀)鍒楅€夐」,瀛︾敓鍕捐嚜宸辨墍鍦ㄧ殑缁?- **personal(day) 閫氱敤瑙勫垯**:鏃犵粍璇惧崱=鍏ㄧ彮蹇呬慨涓€寰嬫樉绀?鏈夌粍璇惧崱蹇呴』鍛戒腑閫夎鐨?(family, group, teacher) 涔嬩竴(鑰佸笀瀹芥澗鍖归厤:閫夎鑰佸笀 鈭?璇惧崱鑰佸笀闆嗗悎)
- ** dismissed DDL**:棣栭〉/璇剧▼椤靛乏婊戠Щ闄?鈫?`ddl_dismissed` 琛ㄦ寜 `title|due_at` 璁?璁剧疆椤?宸茬Щ闄ょ殑浣滀笟"鍙仮澶?`ddl_restore` 鍒犳爣璁?

## 涓冦€佸凡鐭ラ仐鐣欓棶棰?/ 鏈畬鎴?
1. ~~鎻愪氦 ManageBac 404~~ 鈥?宸蹭慨澶嶅苟瀹炴祴瀹氬洜: 鈶犵湡瀹炶矾鐢辨槸 `.../dropbox/upload`
   + 瀛楁 `dropbox[assets_attributes][0][file]`(鍔ㄦ€佽В鏋愬凡瑕嗙洊); 鈶＄敤鎴峰綋鍒濈殑
   404 鍙︽湁涓€灞傚師鍥? agent 鐢ㄤ簡涓婂鏈熺殑杩囨湡 id(39792/88547 宸蹭笉瀛樺湪), 鐜板湪
   submit_task 鏈夊叏璇剧▼閲嶅畾浣嶅厹搴?+ 鍙嬪ソ鎶ラ敊(瑙佺鍥涜妭)
2. ~~TOK 閫氶厤绗﹀鑷村埆鐨勭粍鐨勮鍑虹幇~~ 鈥?宸查€氳繃鏁欏缁勯€夐」瑙ｅ喅,鐢ㄦ埛鍙簿纭嬀閫?3. 娌℃湁鑷姩鍖?CI;鎵撳寘鍚庨渶鎵嬪姩璺?`_ui_test.py` + smoke(2026-09-05 璧锋祦绋?   宸茶窇閫氬苟鍏ュ簱: 瑙?commit fe5bd63)
4. `subject_options()` 鍐峰惎鍔ㄥ彲鑳?60s+(Edupage 鏈嶅姟鍣ㄦ參),鐩墠闈?splash 棰勮浇 + week 纾佺洏缂撳瓨缂撹В;濡傜敤鎴峰弽棣堟參鍙€冭檻鍚庡彴绾跨▼棰勭儹
5. Agent 鐨?`submit_managebac_task` 渚濊禆鍔ㄦ€佽В鏋?+ 閲嶅畾浣?濡傛灉瀛︽牎鏀圭増
   ManageBac 椤甸潰缁撴瀯鍙兘鍐嶆澶辨晥 鈥?灞婃椂閲嶈窇 `_probe_dropbox.py`(鍙)
   鐪嬫柊璺敱/鏂板瓧娈?6. ManageBac 鎻愪氦鐨?*鐪熷疄涓婁紶**浠庢湭鍋氳繃绔埌绔獙璇?鍙獙璇佸埌"鎵惧埌鎻愪氦鍏ュ彛
   鍜?token"杩欎竴姝? 涓嶆嬁鐪熶綔涓氬啋闄?; 鐢ㄦ埛涓嬫鐪熸彁浜ゆ椂鐣欐剰缁撴灉

## 鍏€乬it 鎻愪氦鍘嗗彶(鏈€杩?

```
9c072e1 UI: timetable now-line + logo-to-home + next lesson + chip reorder arrows
fe5bd63 ManageBac fixes: task submit 404 + DDL restore + chip drag reorder
        (鎻愪氦鍏ュ彛鍔ㄦ€佽В鏋?杩囨湡id閲嶅畾浣?/ 璁剧疆椤垫仮澶嶅凡绉婚櫎DDL /
         chip鎸囬拡鐗堟嫋鎷?鍏ㄩ儴chip鍥炲綊淇+缂撳瓨鍚屾 / HANDOFF鍏ュ簱)
39485fd Fix blank page when switching timetable weeks
88ccd2a Timetable UI rework + whole-class courses always shown
75ef86e Drop timetable/home snapshots when selection changes
f155180 Selection rebuilt around teaching groups (family+group+teacher)
ed8952f timetable: class-scoped course source (涔濈彮) + drop hide feature
45f326b timetable: hide-not-mine sections + group-keyed picker
86e6561 timetable: per-room course sections in picker + fix next-Monday leak
32a205b timetable: revert to strict subject+teacher matching
2317198 閭椤甸€氳褰曠鐞?(澧炲垹鏀? + 淇璇捐〃鍛ㄤ簲涓嬪崍涓㈠け
da19fe2 閭閫氳褰? IMAP 鏀跺壊鑱旂郴浜?+ 鍐欓偖浠惰嚜鍔ㄨˉ鍏?+ AI 鎸夊悕瀛楁煡閭
973e2cf 淇: 鎴戠殑璇剧▼椤?NameError 鈥?courses_data 缂哄皯 storage 灞€閮ㄥ鍏?a2f52a5 鏇存柊鏍圭洰褰?exe 鑷虫渶鏂版瀯寤? 娓呯悊鏃?SchoolHub MSI 瑙ｅ寘娈嬬暀
4f45c58 UI 鏀硅繘: 鍒犻椤电┖闂叉暀瀹ゃ€佸啓閭欢寮瑰崱銆亀orkspace 鏂囦欢澶归€夋嫨銆佽绋嬪乏婊戝垹 DDL+鎷栨嫿鎺掑簭
2b04793 鏀瑰悕: SchoolHub 鈫?Hello! Pinghe launcher
2d209bd 鍩虹嚎: 鎬ц兘浼樺寲+缂撳瓨+鏃ョ▼涓夎鍥?璇捐〃鑺傛瀵归綈 (SchoolHub 鍘熷悕)
```

鈿?娉ㄦ剰: 涓婇潰鐨勬彁浜ら『搴忔槸**浠庢柊鍒版棫**, 浣?32a205b(涓ユ牸鍖归厤鍥為€€)瀹為檯鍦?86e6561 涔嬪墠,鍚庢潵 88ccd2a 鐢?鍏ㄧ彮蹇呬慨+鏁欏缁勯€夐」"閲嶆柊瑕嗙洊浜嗕弗鏍煎尮閰嶇殑鍦烘櫙 鈥?璇?git log 鏃舵寜鏃堕棿绾跨悊瑙ｃ€?
## 涔濄€佸瘑鐮?鍑嵁瀹夊叏

- 鐢ㄦ埛鍑嵁(Edupage/ManageBac/閭瀵嗙爜銆佹巿鏉冪爜)瀛樺湪 Windows 鍑嵁绠＄悊鍣?keyring 鏈嶅姟 `hellopinghe`),**涓嶅湪浠讳綍浠ｇ爜鎴栭厤缃枃浠堕噷**
- `~/.hellopinghe/config.json` 鍙湁璐﹀彿鍚嶅拰 AI provider 鐨?api_key(鐢ㄦ埛鑷繁鐨勬満鍣?姝ｅ父)
- 鎵撳寘鍓嶅繀椤昏窇闅愮鎵弿(瑙佹瀯寤哄懡浠?,纭闆跺懡涓墠鑳藉嚭鍖?