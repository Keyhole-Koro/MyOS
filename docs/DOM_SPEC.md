# Kernel DOM 仕様（ISSUE-006 実装時の詰め）

このドキュメントは [ISSUE-006 DOM 的 OS オブジェクトモデル](../../../issues/tickets/dom-like-os.md)
の「実装時に詰める点」を、既存コード（`src/ui/dom.mln`、MyLang の言語機能、
`MyKernel` の heap API）に整合する形で確定させるためのもの。

ここで決めた内容が固まったら、`dom.mln` の実装をこの仕様に合わせて拡張する。
ISSUE-024（UI automation）が要求する `role` / `text` / `bounds` / `state` も本仕様に含める。

## 現状（2026-07-09 時点）

`src/ui/dom.mln` に既に以下がある。

- `Node { id, kind, name, parent, first_child, next_sibling }`（`heap.alloc(16)` で確保）
- `create_node(kind, name)` / `append_child(parent, child)` / `get_node(id)`
- 起動時に root / system / process / device / fs / ui / desktop の固定ノードを生成
- `dump()` によるシリアルへのツリー出力

不足しているのは **props / bounds / text / role / state** と、それらの
読み書き API、ノード削除、hit-test、機械可読 snapshot。

## 前提とする言語・環境制約

既存コードから読み取れる MyLang / kernel の制約。仕様はこれに合わせる。

- `heap.alloc(size)` は生ポインタ（`i32`）を返す。`free(ptr)` あり。**`sizeof` は使わない**
  慣習（既存コードは `alloc(16)` のようにサイズをハードコード）。
- `enum` は使える（`scheduler.mln` / `ssd.mln` で使用実績）。値は明示採番する。
- `bool` 型は使われていない。真偽は **`i32` の 0/1**、または後述の bitmask で表す。
- 固定長配列が基本。struct の配列（`OpenFile g_fd_table[8]`）と、
  parallel array（`task_state[256]` など）の両方が使われている。
- 文字列は `char *` を保持するか、固定長 inline バッファ（`char buf[128]`）。
- **スカラ型は `i32` / `u16` / `u8` / `char` / `void` のみ**（compiler の
  `semantic_types.c` で確認）。**`i16` / `i8` / `u32` / `u64` は存在しない**。
  16bit 幅が欲しいフィールドは `u16`、負値や汎用整数は `i32` を使う。
- **struct レイアウト（実測、2026-07-09）**: `u16`=2B、`char*`=4B、ポインタは
  4B 境界にアライン。**末尾パディングはしない**（下記 `Node` は sizeof=30）。
  したがってフィールド順はアライン穴が空かないよう、`char*` の前で 4B 境界に
  揃うように並べる。
- **compiler の癖（実装時に踏んだ）**: `export u16` なグローバル定数を
  `(i32)STATE_VISIBLE` のように**キャストして参照すると `undefined identifier`**
  になる（semantic が解決できない）。キャストなしで `state & STATE_VISIBLE` や
  関数引数 `set_role(id, ROLE_BUTTON)` として渡すのは OK。state を先に `i32`
  ローカルに入れてから bit 演算する形にすれば回避できる。

## 詰める点と決定

### 1. NodeId の寿命 — 削除済み ID の再利用と世代番号

**決定: 初期実装では ID を再利用しない。世代番号も持たない。**

- `g_next_node_id` を単調増加させるだけ。削除は「slot を無効化する」formで行い、
  同じ ID を新しいノードに割り当て直さない。
- ノード上限は現状の 256 のまま。実験用途では十分。到達したら panic（既存挙動を踏襲）。
- 削除は `remove_node(id)`：親の child リストから外し、`g_node_table[id] = 0`、
  必要なら `heap.free`。ぶら下がった子は同時に再帰削除する（後述の所有権）。

**理由**: 世代番号は use-after-free 検出に有用だが、初期段階の UI ツリーは寿命が単純
（起動時に作って作り直す程度）で、再利用しなければ dangling id が別ノードを指す事故は
起きない。ISSUE-006 本文の「削除済み ID の再利用を許すか。世代番号を持つか」に対し、
**再利用しない = 世代番号不要** を選ぶ。将来 UI の頻繁な作り直しで ID を使い切るなら、
そのとき free list + 世代番号を追加する（`NodeId` を `id:16 | gen:16` にする案）。

### 2. props の表現 — 固定スロット vs key/value 配列

**決定: UI で頻出する props は固定スロットで `Node` に直接持つ。任意 key/value は当面持たない。**

理由: ISSUE-006 本文の props 例（title/x/y/width/height/visible/ownerPid、pid/name/state、
irq/ready）を見ると、種類が有限で UI 描画・automation に直結するものが大半。汎用
key/value テーブルは MyLang に map がなく、実装コストと確保コストが高い。まず固定スロットで
始め、本当に任意属性が要るノード種別が出たら小さな key/value 配列を足す。

拡張後の `Node`（フィールド追加案）:

```c
typedef struct {
    u16 id;
    u16 kind;
    char *name;        // 内部名 / デバッグ用（"root", "desktop" など）
    u16 parent;
    u16 first_child;
    u16 next_sibling;

    // --- UI / automation props（ISSUE-024）---
    u16 role;          // ROLE_* enum。0 = none
    char *text;        // 表示テキスト / accessible name。0 = なし
    u16  x;            // bounds。UI ノード以外では未使用（0）。i16 は無いので u16
    u16  y;            //   （座標は非負前提。負が要るなら i32 に変える）
    u16  w;
    u16  h;
    u16  state;        // STATE_* bitmask（visible/enabled/hovered/pressed/focused）
} Node;
```

`role` を `kind` と別に持つ理由: `kind` は OS 構造上の種別（PROCESS/DEVICE/WINDOW…）、
`role` は accessibility tree 上の役割（button/text…）。多くは対応するが、automation の
locator は `role` で引くため（`get_by_role("button")`）分離しておく。UI ノード生成 helper
（`node_button` など）が `kind` と `role` を両方設定する。

**確保サイズ（実測レイアウト、2026-07-09）**: 上記フィールドのオフセットは
`id=0 kind=2 name=4 parent=8 first_child=10 next_sibling=12 role=14 text=16
x=20 y=22 w=24 h=26 state=28` で **sizeof(Node) = 30**（末尾パディングなし）。
現状の `heap.alloc(16)` を **`heap.alloc(32)`** に変更する。30 でも足りるが、
次ノードが 4B 境界から始まるよう 32 に切り上げておく（`char*` フィールドの
アライン安全のため）。

> 検証方法: `x`/`y` を `i16` にしようとすると compiler がパースエラー（`i16` 未定義）。
> 実測は一時 probe（`&node[1] - &node[0]` で stride、各フィールドは `&f - base`）で取得。

### 3. 文字列管理 — heap 上 vs 固定長 inline

**決定: `name` と `text` はどちらも `char *`（heap またはリテラル領域を指すポインタ）。inline 固定長は持たない。**

- 静的な `name`（"root" 等）は string リテラル領域を指すだけ（現状どおり）。コピーしない。
- 動的な `text`（"clicks: 3" など毎フレーム変わる値）は、呼び出し側が用意した
  バッファを指させる。DOM 側では文字列を所有・コピーしない（初期段階）。
- `set_text(node, ptr)` はポインタを差し替えるだけ。文字列の寿命は呼び出し側責任。

**理由**: inline 固定長（例 `char text[32]`）は `Node` を大きくし、確保サイズが膨らむ。
初期段階では「テキストは呼び出し側が持つバッファを指す」で足りる。カウンタ表示のように
数値を毎回整形する場合は、描画側が整形して `set_text` するか、`Text` ノードに数値 prop を
持たせて renderer が整形する（後者は props 拡張時に検討）。将来、プロセスをまたいで
文字列を安定保持したくなったら DOM 所有の string arena を導入する。

### 4. 権限 — 他プロセス所有ノードの書き換え

**決定: 初期実装では権限チェックをしない。全ノードがカーネル空間で共有され、誰でも読み書きできる。**

- `Node` に `ownerPid` prop を持たせる余地は残す（props 固定スロットに後で追加可）が、
  初期は enforcement しない。
- カーネルと単一の UI ループ・shell タスクしかいない現状ではプロセス分離が未成熟なので、
  権限モデルは prosess 分離が実装されてから（ISSUE-006 と別軸で）詰める。

**理由**: ISSUE-006 本文も「権限」を将来課題として挙げている。今これを入れると、まだ存在
しないプロセス境界に対する空振りチェックになる。ownerPid は props として記録だけしておき、
enforcement は後回し。

### 5. イベントキュー — グローバル 1 本 vs プロセスごと

**決定: グローバル 1 本のイベントキューにする。**

- 既存の `mouse.mln` が既にグローバルな circular queue（`q_x[64]` など）を持っており、
  DOM イベントもこれに倣う。
- `Event { type, target, current, x, y, key, data0, data1 }`（ISSUE-006 の構造案）を
  グローバル固定長リングバッファに積む。
- 配送は「target ノードへ渡し、必要なら親へ上げる」だけ。capture/bubble の完全実装はしない。

**理由**: プロセスごとのキューはプロセス分離が前提。現状は単一 UI ループがイベントを
drain する構造（`main.mln` の `ui_loop`）なので、グローバル 1 本が最も素直。将来
マルチプロセス UI にするとき、per-process キュー + フォーカス管理を足す。

### 6. renderer の責務 — window manager と分けるか

**決定: 初期は renderer と window manager を一体にする。renderer は UI subtree を走査して描画するだけ。**

- renderer は `ui/desktop` 配下の UI ノード（WINDOW/BOX/TEXT/BUTTON）を DFS で走査し、
  各ノードの `bounds` と `state` を見て `graphics.*` を呼ぶ。
- z-order は「ツリーの兄弟順 = 描画順」（後の兄弟が上）。独立した window manager レイヤは
  作らない。
- hover/press などの見た目差分は `state` bitmask を見て renderer が分岐する
  （現 `main.mln` の `hover` 分岐を DOM 駆動に移す）。

**理由**: ウィンドウの重なり・移動・フォーカスを扱う本格的な WM はまだ要らない。
まず「DOM ツリーを描ける」ことを最優先し、WM 相当の責務（ドラッグ移動・前面化）は
イベント処理（フェーズ3）以降に切り出す。

## 追加で確定させる enum / bitmask

### role（automation 用、ISSUE-024）

```c
enum Role {
    ROLE_NONE    = 0,
    ROLE_ROOT    = 1,
    ROLE_DESKTOP = 2,
    ROLE_WINDOW  = 3,
    ROLE_BUTTON  = 4,
    ROLE_TEXT    = 5
}
```

### state（bitmask、`u16 state`）

```c
u16 STATE_VISIBLE = 1;   // 1 << 0
u16 STATE_ENABLED = 2;   // 1 << 1
u16 STATE_HOVERED = 4;   // 1 << 2
u16 STATE_PRESSED = 8;   // 1 << 3
u16 STATE_FOCUSED = 16;  // 1 << 4
u16 STATE_HIT_TESTABLE = 32; // 1 << 5
```

`bool` がないため bitmask を `i32` 演算で読み書きする（`state & STATE_HOVERED`）。

## 新規 API（この仕様で追加するもの）

```c
// bounds / text / role / state アクセサ
export void set_bounds(i32 id, i32 x, i32 y, i32 w, i32 h);
export void set_text(i32 id, char *text);
export void set_role(i32 id, i32 role);
export void set_state_flag(i32 id, i32 flag, i32 on);  // on!=0 で立てる、0 で落とす
export i32  get_state_flag(i32 id, i32 flag);          // 立っていれば 1

// UI ノード生成 helper（kind と role をまとめて設定）
export i32 create_window(char *title, i32 x, i32 y, i32 w, i32 h);
export i32 create_button(char *label, i32 x, i32 y, i32 w, i32 h);
export i32 create_text(char *text, i32 x, i32 y);
export i32 create_box(i32 x, i32 y, i32 w, i32 h,
                      i32 background, i32 border);
export i32 create_column(i32 x, i32 y, i32 w, i32 h,
                         i32 padding, i32 gap);

// hit-test（automation の click / mouse 配送で使う）
export i32 find_at(i32 x, i32 y);  // 最前面の当たりノード id。なければ 0

// 削除
export void remove_node(i32 id);   // 子も再帰削除、slot 無効化（ID 再利用しない）

// 機械可読 snapshot（ISSUE-024 の DOM snapshot）
export void snapshot();            // 1 行 1 ノードの JSON をシリアルへ
```

`dump()`（人間可読）は残し、`snapshot()`（機械可読 JSON Lines）を別に追加する。
snapshot の 1 行フォーマットは ISSUE-024 の例に合わせる:

```json
{"id":4,"role":"button","name":"CLICK ME","text":"CLICK ME","x":120,"y":130,"w":150,"h":60,"visible":true,"enabled":true}
```

## Box と Column（2026-09-08）

`Box` は背景色と枠線を描画する装飾コンテナ、`Column` は直下の子を縦に並べる
レイアウトコンテナである。どちらも accessibility role は持たず、既定では
hit-test 対象にもならない。これにより、背景用の Box や領域用の Column が内部の
Button を覆い隠さない。

```mylang
<Box x={100} y={100} w={280} h={160} background={0xEEEEEE} border={0x888888}>
    <Column x={116} y={116} w={248} h={128} padding={0} gap={8}>
        <Button text="Save" x={0} y={0} w={96} h={28} onClick={save} />
        <Text text="Changes are saved" x={0} y={0} />
    </Column>
</Box>
```

- `Box` は `background` と `border` に `graphics.rgb()` と同じ packed RGB 値を取る。
- `Column` は子の `x/y` を `column.x + padding` と、前の子の高さ＋`gap`から決める。
  子自身の `w/h` は保持する。`Text` は `h=0`でも8pxの行高を消費する。
- Column を動かす、padding/gapを変更する、子の高さを変更する場合は直ちに再配置する。
- `Button` は既定で `STATE_HIT_TESTABLE`。汎用ノードを操作対象にしたい場合は
  `set_hit_testable(id, 1)` または `set_on_click(id, handler)` を使う。

## 実装フェーズ（この仕様に基づく）

1. ✅ **完了（2026-07-09）** `Node` にフィールド追加（role/text/x/y/w/h/state）、
   `alloc(16)`→`alloc(32)`。`create_node` で新フィールドを 0 初期化。`dump()` に
   props を出す（`dump_props`）。カーネル起動で dump 確認済み、props 付きノードが
   `[role=4 x=120 y=130 w=150 h=60 vis en]` と表示されることを検証済み。→ **フェーズ1完成**
2. 🟡 **一部（2026-07-09）** アクセサ `set_bounds`/`set_text`/`set_role`/
   `set_state_flag`/`get_state_flag` は実装・検証済み。UI helper
   `create_window`/`create_button`/`create_text` はまだ（次の作業）。
3. renderer：`ui/desktop` 配下を走査して `graphics.*` で描画。
   `main.mln` の `render_scene` を DOM 駆動に置き換える。→ **フェーズ2**
4. `find_at` hit-test と、mouse event の DOM 配送。→ **フェーズ3 の入口**
5. `snapshot()` と emulator control mode 連携。→ **ISSUE-024**

## インターフェイス具体例

API シグネチャだけでは使い勝手が見えないので、「呼ぶ側がどう書くか」「内部でどう動くか」
「出力がどう見えるか」を具体的に示す。

### 例1: UI ツリーの組み立て（呼ぶ側）

現状の `main.mln` はボタン座標を `BTN_X=120` のようにグローバル定数で持ち、
`render_scene` が `graphics.fill_rect(...)` を直接叩いている。これを DOM ツリーの
組み立てに置き換える。

```c
// desktop ノードは init() が既に作っている。その id を取得して window をぶら下げる。
i32 desktop = dom.desktop_id();               // 新規: 固定ノードの id 取得 helper

i32 win = dom.create_window("MyKernel Window", 100, 100, 600, 400);
dom.append_child(desktop, win);

i32 btn = dom.create_button("CLICK ME", 120, 130, 150, 60);
dom.append_child(win, btn);

i32 label = dom.create_text("clicks: 0", 320, 150);
dom.append_child(win, label);
```

`create_button("CLICK ME", 120, 130, 150, 60)` の内部は、既存の低レベル API を
組み合わせるだけ:

```c
export i32 create_button(char *label, i32 x, i32 y, i32 w, i32 h) {
    i32 id = create_node(NODE_BUTTON, label);  // kind=NODE_BUTTON, name=label
    set_role(id, ROLE_BUTTON);                 // role=button（automation 用）
    set_bounds(id, x, y, w, h);                // x/y/w/h スロットへ
    set_text(id, label);                       // text=label（表示 & accessible name）
    set_state_flag(id, STATE_VISIBLE, 1);
    set_state_flag(id, STATE_ENABLED, 1);
    return id;
}
```

`set_bounds` / `set_state_flag` の中身も素直:

```c
export void set_bounds(i32 id, i32 x, i32 y, i32 w, i32 h) {
    Node *n = get_node(id);
    if ((i32)n != 0) {
        n->x = (u16)x; n->y = (u16)y;
        n->w = (u16)w; n->h = (u16)h;
    }
}

export void set_state_flag(i32 id, i32 flag, i32 on) {
    Node *n = get_node(id);
    if ((i32)n != 0) {
        if (on != 0) { n->state = (u16)((i32)n->state | flag); }
        else         { n->state = (u16)((i32)n->state & (~flag)); }
    }
}
```

### 例2: renderer（DOM を走査して描画）

`render_scene(clicks, hover)` を廃止し、renderer は DOM ツリーを走るだけにする。
描画すべき見た目の状態（hover 等）はノードの `state` に載っているので引数で渡さない。

```c
// ui/desktop 配下を DFS で描画。呼ぶ側は render(dom.desktop_id()) だけ。
export void render(i32 node_id) {
    Node *n = get_node(node_id);
    if ((i32)n == 0) { return; }

    // visible でなければ自身も子孫も描かない。
    if (((i32)n->state & STATE_VISIBLE) == 0) { return; }

    draw_node(n);   // kind ごとに graphics.* を呼ぶ

    // 兄弟順 = z-order（後の子が上）。子を順に描く。
    i32 child_id = (i32)n->first_child;
    while (child_id != 0) {
        render(child_id);
        Node *c = get_node(child_id);
        child_id = (i32)c->next_sibling;
    }
}

void draw_node(Node *n) {
    i32 kind = (i32)n->kind;
    i32 x = (i32)n->x; i32 y = (i32)n->y;
    i32 w = (i32)n->w; i32 h = (i32)n->h;

    if (kind == NODE_WINDOW) {
        i32 white = graphics.rgb(255, 255, 255);
        graphics.fill_rect(x, y, w, h, white);
        graphics.draw_rect(x, y, w, h, graphics.rgb(136, 136, 136));
        graphics.draw_text(x + 20, y + 8, (char*)n->text, graphics.rgb(0,0,0), white);
    } else if (kind == NODE_BUTTON) {
        // state を見て見た目を変える（旧 render_scene の hover 分岐を DOM 駆動に）。
        i32 hovered = (i32)n->state & STATE_HOVERED;
        i32 face = graphics.rgb(204, 204, 204);
        if (hovered != 0) { face = graphics.rgb(180, 210, 255); }
        graphics.fill_rect(x, y, w, h, face);
        graphics.draw_rect(x, y, w, h, graphics.rgb(0,0,0));
        graphics.draw_text(x + 30, y + 22, (char*)n->text, graphics.rgb(0,0,0), face);
    } else if (kind == NODE_TEXT) {
        graphics.draw_text(x, y, (char*)n->text, graphics.rgb(0,0,0), graphics.rgb(255,255,255));
    }
    // NODE_WINDOW/BUTTON/TEXT 以外（PROCESS/DEVICE 等）は描かない。
}
```

`ui_loop` は「イベント drain → hit-test で hover/click state 更新 → `render(desktop)` →
`present()`」に変わる。座標定数 `BTN_X` 等と `render_scene` の引数渡しが消える。

### 例3: hit-test（automation / mouse で使う）

```c
// (x,y) を含む最前面の UI ノード id を返す。なければ 0。
// 後の兄弟が手前なので、子は逆順に見て最初に当たったものを採用する。
export i32 find_at(i32 x, i32 y) {
    return hit_test(g_root_node_id, x, y);
}
```

呼ぶ側:

```c
i32 hit = dom.find_at(mx, my);
if (hit != 0) {
    Node *n = dom.get_node(hit);
    if ((i32)n->role == ROLE_BUTTON) {
        // hover を立てる / click 時にカウンタ更新
        dom.set_state_flag(hit, STATE_HOVERED, 1);
    }
}
```

### 例4: dump() と snapshot() の出力（見え方）

`dump()`（人間可読、既存を props 付きに拡張）:

```
--- Kernel Object Tree ---
Node(id=1, kind=0) root
  Node(id=2, kind=1) system
    Node(id=3, kind=2) process
    Node(id=4, kind=3) device
      Node(id=5, kind=4) fs
    Node(id=6, kind=6) ui
      Node(id=7, kind=7) desktop
        Node(id=8, kind=8) MyKernel Window  [x=100 y=100 w=600 h=400 vis]
          Node(id=9, kind=11) CLICK ME       [role=button x=120 y=130 w=150 h=60 vis en]
          Node(id=10, kind=10) clicks: 0     [role=text x=320 y=150 vis]
--------------------------
```

`snapshot()`（機械可読、automation client がツリーへ復元。1 行 1 ノードの JSON Lines）:

```json
{"id":8,"kind":8,"role":"window","name":"MyKernel Window","text":"MyKernel Window","x":100,"y":100,"w":600,"h":400,"parent":7,"visible":true,"enabled":true}
{"id":9,"kind":11,"role":"button","name":"CLICK ME","text":"CLICK ME","x":120,"y":130,"w":150,"h":60,"parent":8,"visible":true,"enabled":true}
{"id":10,"kind":10,"role":"text","name":"clicks: 0","text":"clicks: 0","x":320,"y":150,"w":0,"h":0,"parent":8,"visible":true,"enabled":true}
```

これで ISSUE-024 の Python client が `get_by_role("button", name="CLICK ME")` →
id=9 を特定 → bounds 中心 (195,160) に mouse 注入、という流れが組める。

### API 早見表

| API | 何をするか | 使う場面 |
|---|---|---|
| `create_node(kind, name)` | 低レベル：ノード確保、id 採番 | helper の内部 |
| `create_window/button/text(...)` | kind+role+bounds+text+state をまとめて設定 | UI 組み立て |
| `append_child(parent, child)` | 子リスト末尾に繋ぐ | UI 組み立て |
| `set_bounds/set_text/set_role` | props スロットへ書き込み | 生成・更新 |
| `set_state_flag(id, flag, on)` | state bit を立てる/落とす | hover/press/focus 更新 |
| `get_state_flag(id, flag)` | state bit を読む（0/1） | renderer / 判定 |
| `get_node(id)` | Node* を得る（無効なら 0） | 全般 |
| `find_at(x, y)` | 座標→最前面ノード id | mouse 配送 / click |
| `remove_node(id)` | 子ごと削除、slot 無効化 | UI 作り直し |
| `dump()` | 人間可読ツリー出力 | デバッグ |
| `snapshot()` | JSON Lines 出力 | automation |
| `desktop_id()` | 固定 desktop ノードの id | UI のぶら下げ先 |

## 検証（この仕様の受け入れ）

1. 起動時ツリーに props（bounds/text/state）が乗り、`dump()` / `snapshot()` に出る。
2. `create_button(...)` で作ったノードが `role=button` / bounds / text を持つ。
3. renderer が DOM から window / button / text を描画する（`main.mln` 座標直書きを廃止）。
4. `find_at(cx, cy)` が button ノードを返し、click で `STATE_PRESSED` と counter が更新される。
5. `snapshot()` の JSON を外部（automation client）がツリーへ復元できる。
