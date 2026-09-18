# MyOS アプリケーションフレームワーク

`src/app/` と、コンパイラの `@app` 属性で成り立つ、デスクトップアプリの書き方と
その裏側。文法側の仕様は
`toolchain/MyLangCompiler/docs/grammar.md` の "Attributes and applications" と
`source-modifiers.md` の DOM lowering contract にある。

## アプリの書き方

```mylang
package counter;

import dom_elements from "../ui/dom/dom_elements.mln";   // markup の要素語彙
import ui from "../app/ui.mln";                          // アプリが呼べる UI API

@app
struct Counter {
    i32 clicks = 0;      // インスタンスの状態。フィールド初期化子は literal のみ
    i32 step = 1;
    i32 label;           // ref= で受け取るノード id
};

i32 (Counter *c) view() {                                // 必須: ウィンドウを返す
    return <Window title="Counter" x={96} y={72} w={400} h={272}>
        <Label ref={c->label} text="clicks: 0" bold={1} testId="counter" />
        <PrimaryButton text="Click me" w={120} h={36} onClick={c->click} />
    </Window>;
}

void (Counter *c) click(i32 id) {
    c->clicks = c->clicks + c->step;
    ui.set_text_fmt(c->label, "clicks: %d", c->clicks, 0);
}
```

- **ファイルを `src/apps/` に置くだけで登録される。** ビルド
  （`qa/runners/gen_app_manifest.py`）が `@app` を拾って
  `build/apps_manifest.mln` を生成し、`boot/main.mln` がそれを 1 回呼ぶ。
  アプリは framework を import しないし、framework もアプリを知らない。
- **レシーバはポインタ** (`Counter *c`)。ディスパッチャはインスタンスを i32 で
  持っているので、`ref mut` レシーバでは受け取れない（コンパイルエラーになる）。
- **`ref={c->label}`** はそのノードの id をフィールドに書く。木を歩いて id を
  取り直すコードは要らない。
- **省略できるプロパティ**: `x`/`y`（親原点）、`w`/`h`、`color`、`bold`、
  `gap`、`padding`、`onClick` 等は `dom_elements.mln` のデフォルトが入る。
  `testId="..."` は automation 用の名前。
- **ハンドラはメソッドを直接渡す** (`onClick={c->click}`)。引数は
  `()`, `(i32 id)`, `(i32 id, i32 arg)` のどれでもよい。

### メソッド属性

| 属性 | 意味 | 形 |
| --- | --- | --- |
| `@app` / `@app(single)` / `@app(name = "...")` | アプリ宣言。`single` は 2 回目の起動で既存ウィンドウを前面に | struct |
| `@open` | 他アプリの `ui.open(path)` を受ける。`single` なら既存インスタンスへ | `(char *path)` |
| `@on_close` | ユーザーがウィンドウを閉じた。後始末のあと framework がインスタンスを解放 | `()` / `(i32 id)` |
| `@timer(ms)` | mount 中、周期的に呼ばれる。インスタンスごとに 1 本 | `()` / `(i32 id)` |
| `@key("Ctrl+S")` | そのウィンドウがアクティブな間のショートカット | `()` / `(i32 id)` / `(id, arg)` |
| `@task` | 予約（scheduler がタスク引数を取れるようになったら） | `()` |

`src/apps/editor.dom.mln`（`single` + `@open` + `@key`）、`terminal.dom.mln`
（`@timer` + `@key("Enter")` + `@on_close`）、`files.dom.mln`（ダイアログ）が実例。

## アプリが触れるもの

アプリが import するのは `dom_elements.mln`（markup の解決先。コードからは呼ばない）
と `app/ui.mln` だけ。`ui` の引数・戻り値は **i32 と char\* のみ**（無しは
インデックスなら -1、id/ポインタなら 0）で、struct・Option・Node\* は跨がない。
ユーザープロセスでアプリを動かすとき、この面をそのまま syscall にするため。

- テキスト: `set_text`, `text_of`, `set_text_fmt(id, "%d / %s", a, b)`
  （ラベルはポインタを保持するので、書式結果はラベルごとのバッファに置かれる）
- ウィジェット: `is_checked`, `set_checked`, `input_set`, `area_append`,
  `area_clear`, `list_set_items`, `list_selected` (-1), `list_item`
- ウィンドウ: `focus`, `focus_window`, `show(win)`（自作ダイアログを載せる）,
  `close(win)`, `window_of`, `window_x/y`
- 他アプリ: `open(path)` → `@open` を持つアプリへ

## 裏側

```
App (src/apps/*.mln, @app)
 ↕ ui.mln — id + scalar。イベントはキュー経由
UI server: dom.mln / dom_elements / dom_widgets / dom_render / dom_automation
 ↕ damage rect（Surface 1 枚 = 画面全体）
compositor.mln: 入力 → hit-test → キュー、paint / present
```

- **ハンドラ ABI は 1 種類**: `void handler(i32 owner, i32 id, i32 arg)`。
  `owner` はインスタンスのポインタ。コンパイラが `onClick={c->click}` や属性付き
  メソッドからこの形のトランポリン (`Counter__click__tramp`) を生成する。
  `dom.set_on_click` 等を直接呼ぶ低レベルコードだけがこの ABI を手書きする。
- **イベントキュー** (`dom.push_event` / `dom.drain_events`): クリック・変更・
  タイマは検出した場所で呼ばず、コンポジタが入力処理の後にまとめて実行する
  （Phase A: 同じタスク上）。別タスクへ移すのが隔離の次の一歩で、その時に DOM
  ロックが要る。`on_key` フィルタだけは「キーを widget に渡すか」を即決するので
  インラインのまま。
- **owner**: `Node.owner` は生成時に `dom.g_current_owner` が入る。framework は
  `view()` とハンドラの実行中それをインスタンスに設定するので、アプリが途中で
  作ったダイアログやタイマも同じ owner になり、ウィンドウを閉じると
  `dom.remove_owned(owner)` で一括回収される。
- **ディスクリプタ**: `__app_Counter_desc()` が返す i32 の表
  （名前, flags, size, init, view, open, on_close, task, timer 数, key 数,
  (interval, fn)…, (mods, code, fn)…）。`app.mln` はこの表だけを読む。
- **manifest** が唯一「アプリと framework の両方を知る」場所。コンパイルは
  `main.mln` からの import 追跡で決まり、リンカにセクションが無いので、宣言だけで
  表に載る仕掛けは作れない。生成ファイルで代替している。

## 検証

```
make build
python3 system/MyOS/tests/app_framework_test.py   # launcher / single / @key / @open / close / dialog
python3 system/MyOS/tests/dom_click_test.py       # Counter と Notes の操作
python3 system/MyOS/tests/apps_e2e_test.py        # プロセス / エディタ / ファイラ
```

## 既知の制限

- ノード id は再利用されず 256 で尽きる（`dom.mln`）。アプリの起動・終了を
  繰り返すと `dom: out of node ids` で止まる。
- `f->items[0]` のように、ポインタ経由の配列フィールドは添字できない。
  バッファはモジュール変数か heap に置く（`files.dom.mln` 参照）。
- `Result<Option<i32>, E>` を値の case で受ける (`Ok(v) -> v`) と struct が
  コピーされない。文レベルの 2 段 case で読む（`files.dom.mln` の `refresh`）。
- `@task` は framework 側が未実装（`scheduler.spawn_task` がタスク引数を取らない）。
