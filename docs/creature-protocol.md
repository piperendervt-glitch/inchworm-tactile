# 人工生物 通信仕様（Creature Link Protocol）v1

作成日: 2026-09-10
共有先: `mech-arena-quest/docs/creature-protocol.md` と `inchworm-tactile/docs/creature-protocol.md`（同一内容を保つ。変更は両方に同時反映し、必要なら `v` を上げる）

関連: `mech-arena-quest/docs/learning-monster-design.md` §10

## 1. 役割

| 側 | 名称 | 担当 |
|---|---|---|
| Unity（mech-arena-quest） | **Body** | 個体の身体・触覚クエリ・移動・HP・捕食ダメージ・描画・個体の生成と消滅 |
| Python（inchworm-tactile） | **Brain** | 脳（方策）・生理（エネルギー）・スティグマジー場・ログ記録・学習 |

原則: **Body は観測を送り、Brain は指令を返す。** Brain は環境の真実を持たず、Body は行動の判断を持たない。

## 2. トランスポート

- UDP、IPv4。Brain が待ち受け、Body が送信元になる。Brain は最後に受信した送信元アドレスへ返信する。
- 既定ポート: **47123**（Brain 側）。Body は任意ポート。
- 1 データグラム = 1 メッセージ。UTF-8 の JSON テキスト。**最大 60,000 バイト**。
- 圧縮なし。順序保証なし。欠落は許容する（後述の tick で判定）。
- 接続先 IP は Body 側の設定値。既定 `127.0.0.1`。Quest 実機から PC へ送る場合は PC の LAN IP。

## 3. 共通フィールド

すべてのメッセージは以下を持つ。

```json
{ "v": 1, "type": "<種別>", "session": "<セッションID>", "tick": 123 }
```

| フィールド | 型 | 意味 |
|---|---|---|
| `v` | int | プロトコル版。不一致の場合、受信側は `error` を返して無視する |
| `type` | string | `hello` / `welcome` / `observe` / `command` / `field` / `bye` / `error` |
| `session` | string | Body が `hello` で発行する UUID。以後すべてに付ける。Brain は session が変わったら状態を全消去する |
| `tick` | int | Body の観測 tick（0 始まり、30 Hz で +1）。`command` は対応する `observe` の tick を返す |

## 4. 座標系と単位

- 位置は **Unity ワールド座標** をそのまま送る。単位メートル。
- 平面は XZ 平面。`pos` は `[x, y, z]` の 3 要素だが、Brain は原則 `x` と `z` だけを使う。
- `heading` は **Y 軸まわりの回転角、ラジアン**。0 で +Z 方向を向く。上から見て**時計回りが正**（Unity の yaw と同じ）。
- Brain 内部で数学座標に直す場合は Brain の責任で変換する（`x_math = x`, `y_math = z`, `theta_math = -heading`）。Body は変換しない。
- 時間は秒。`dt` は前 tick からの経過時間で、Body が 0.1 秒を上限にクランプする。

検算（heading → 前進方向ベクトル `[sin(heading), cos(heading)]`）:

| heading | 前進方向 (x, z) |
|---|---|
| 0 | (0, 1) |
| π/2 | (1, 0) |
| π | (0, -1) |

## 5. 触覚セル配置

- 個体は長軸を進行方向に向けたカプセル。触覚セルは **16 個**、周方向 8 × 前後 2 段。
- セル番号 `i = segment * 8 + ring`。`segment` 0 = 前段、1 = 後段。`ring` 0 = 真上（+Y）から、**前から見て時計回り**に 45° 刻み。
- したがって `ring` 2 = 右側面、4 = 真下、6 = 左側面。
- セル半径やカプセル寸法は `hello.body` で通知する。Brain はセル数以外に依存しない。

各セルの値:

| フィールド | 型 | 範囲 | 意味 |
|---|---|---|---|
| `p` | float | 0..1 | 接触圧。`1 - exp(-gain * 侵入深さ)`。非接触は 0 |
| `k` | int | 0..3 | 接触相手種別: 0 なし / 1 壁・構造物・地面 / 2 餌（プレイヤー・敵機・残骸。区別しない） / 3 他個体 |
| `h` | float | 0..1 | このセル付近で今 tick 受けた被弾の強さ。ダメージ / 最大HP。なければ 0 |

`k` は同時接触があれば優先順位 2 > 3 > 1。

### 5.1 聴覚（`ears`）

耳は 4 つ。**方策の入力としてだけ渡し、音へ向かう反射は持たせない**（向かうかどうかは学習で決まる）。計算は Body（`CreatureHearing.cs`）と Brain の再生シミュレータ（`creature_sim/hearing.py`）で同一。

- 音源ごとの大きさ: `loudness = level / (1 + (距離 / 8)^2)`、距離 45 m 超は 0
- 耳 `i` の向き: `heading + i × 90°`（0 前 / 1 右 / 2 後 / 3 左）
- 方位: `bearing = atan2(dx, dz)`（§4 の heading と同じ規約）
- 耳の値: `Σ loudness × max(0, cos(bearing − 耳の向き))` を 0..1 にクランプ。音源と同位置なら全耳に `loudness`

音源の `level`（0..1）:

| 音源 | level |
|---|---|
| プレイヤー | `0.2 + 0.5 × clamp01(速度 / 12) + (ブースト中 0.2) + 射撃音`、1 でクランプ。射撃音は 0.6 から 0.5 秒で 0 へ減衰 |
| 敵機 Strider | 0.45（攻撃チャージ中 +0.4） |
| 敵機 Bastion | 0.30（攻撃チャージ中 +0.4） |
| 残骸 | 音を出さない |

検算（level 1）:

| 個体 heading | 音源の相対位置 (dx, dz) | ears |
|---|---|---|
| 0 | (0, 8) | [0.5, 0, 0, 0] |
| 0 | (8, 0) | [0, 0.5, 0, 0] |
| π/2 | (8, 0) | [0.5, 0, 0, 0] |
| 0 | (0, −4) | [0, 0, 0.8, 0] |
| 0 | (5.657, 5.657) | [0.3536, 0.3536, 0, 0] |

## 6. メッセージ

### 6.1 `hello`（Body → Brain、接続開始と再接続時）

```json
{
  "v": 1, "type": "hello", "session": "3f2a...", "tick": 0,
  "build": "0.5.0-creature",
  "hz": 30,
  "body": { "length": 3.0, "radius": 0.8, "cellRadius": 0.4, "cells": 16, "runSpeed": 6.0, "maxHealth": 120 },
  "player": { "maxHealth": 250 },
  "arena": {
    "bounds": [-40, -40, 40, 40],
    "obstacles": [
      { "shape": "circle", "center": [3.0, -5.0], "radius": 1.5 },
      { "shape": "box", "center": [10.0, 2.0], "size": [4.0, 2.0], "yaw": 0.0 }
    ]
  }
}
```

- `arena.bounds` は `[minX, minZ, maxX, maxZ]`。スティグマジー場はこの矩形に貼る。
- `arena.obstacles` は Brain の**再生シミュレータ用**。リアルタイム推論には使わない（生物は目が見えない）。
- Brain は `hello` を受けたら当該 session の状態を初期化し `welcome` を返す。

### 6.2 `welcome`（Brain → Body）

```json
{
  "v": 1, "type": "welcome", "session": "3f2a...", "tick": 0,
  "brain": { "species": "ecoli", "generation": 4, "fingerprint": "a91c...", "inputs": 104, "outputs": 2 },
  "field": { "cols": 64, "rows": 64, "channels": 4 },
  "maxCreatures": 12
}
```

Body は `welcome` を受けるまで個体を静止させ、HUD に `BRAIN OFFLINE` を表示する。

### 6.3 `observe`（Body → Brain、毎 tick 30 Hz）

```json
{
  "v": 1, "type": "observe", "session": "3f2a...", "tick": 1234,
  "t": 41.13, "dt": 0.0333,
  "player": { "pos": [1.2, 0.0, -3.4], "heading": 0.52, "hp": 210, "guard": false },
  "creatures": [
    {
      "id": 3,
      "state": "alive",
      "pos": [5.0, 0.0, 2.0], "heading": 1.57, "speed": 4.1,
      "hp": 95,
      "eating": true,
      "ears": [0.12, 0.0, 0.0, 0.31],
      "cells": [
        { "p": 0.0, "k": 0, "h": 0.0 },
        { "p": 0.6, "k": 2, "h": 0.0 }
      ]
    }
  ],
  "sounds": [
    { "kind": "player", "pos": [1.2, 0.0, -3.4], "level": 0.45 },
    { "kind": "mech", "pos": [12.0, 0.0, 8.0], "level": 0.45 }
  ],
  "prey": [
    { "kind": "mech", "id": 0, "pos": [12.0, 0.0, 8.0], "hp": 100, "maxHp": 100 },
    { "kind": "wreck", "id": 2, "pos": [-6.0, 0.0, 14.0], "hp": 270, "maxHp": 300 }
  ]
}
```

| フィールド | 意味 |
|---|---|
| `t` | セッション開始からの秒 |
| `player` | **Brain のログと学習にのみ使う。方策の入力に入れてはならない** |
| `creatures[].id` | Body が付与する 0 以上の整数。プール再利用時も同じ id を使ってよいが、`state` が `spawned` の tick で Brain は個体状態をリセットする |
| `state` | `spawned`（この tick で出現）/ `alive` / `dead`（この tick で死亡。次 tick から一覧に含めない） |
| `reason` | `state == dead` のときのみ。`shot`（被弾）/ `starved`（Brain 指示）/ `despawn`（ミッション終了など） |
| `speed` | 実際の水平速度 m/s |
| `eating` | `k == 2` のセルが 1 つ以上ある間 true。捕食ダメージは Body が与える: 接触 0.5 秒後に最初の一口、以後 1 秒に 1 口、1 口で相手の最大 HP の 1/10（切り上げ）。約 10 口で倒れる |
| `ears` | 4 要素 0..1（前・右・後・左）。§5.1。省略時 Brain は無音として扱う |
| `cells` | 16 要素。省略不可 |
| `sounds` | この tick の音源一覧。**ログと再生シミュレータ用。方策の入力に入れてはならない**（方策には `ears` だけが届く） |
| `prey` | プレイヤー以外の餌（`mech` / `wreck`）の位置と HP。**ログと再生シミュレータ用。方策の入力に入れてはならない** |

### 6.4 `command`（Brain → Body、`observe` ごとに 1 通）

```json
{
  "v": 1, "type": "command", "session": "3f2a...", "tick": 1234,
  "creatures": [
    { "id": 3, "mode": "run",    "speed": 1.0, "turn": 0.0,  "deposit": 0.2, "energy": 0.61, "starved": false },
    { "id": 4, "mode": "tumble", "speed": 0.0, "turn": -2.4, "deposit": 0.0, "energy": 0.05, "starved": false },
    { "id": 5, "mode": "idle",   "speed": 0.0, "turn": 0.0,  "deposit": 0.0, "energy": 0.0,  "starved": true }
  ]
}
```

| フィールド | 型 | 範囲 | 意味 |
|---|---|---|---|
| `mode` | string | `run` / `tumble` / `idle` | 行動。Body は `run` で前進、`tumble` で旋回、`idle` で停止 |
| `speed` | float | 0..1 | `runSpeed` に対する比率 |
| `turn` | float | rad/s | 旋回速度。時計回りが正（heading と同じ向き） |
| `deposit` | float | 0..1 | スティグマジー書き込み強度。Body は表示にだけ使う（場は Brain 側） |
| `energy` | float | 0..1 | 表示用。エネルギー残量比 |
| `starved` | bool | | true なら Body はその個体を `reason: "starved"` で殺す |

- Body は **最後に受け取った `command` を次が来るまで保持**して適用する。tick が既知より古い `command` は捨てる。
- `command` を **1.0 秒**受け取れなければ Body は全個体を `idle` にし `BRAIN OFFLINE` を表示する。復帰後は Body が `hello` を再送する（session は新規発行）。
- `observe` に含まれない id が `command` にあれば Body は無視する。

### 6.5 `field`（Brain → Body、0.25 秒ごとに 1 チャネル、任意）

```json
{
  "v": 1, "type": "field", "session": "3f2a...", "tick": 1234,
  "cols": 64, "rows": 64, "channel": 0,
  "origin": [-40.0, -40.0], "cellSize": 1.25,
  "data": "<base64: rows*cols バイト、値 0..255>"
}
```

- 行優先。`data[row * cols + col]` がセル `(col, row)`。`col` は X、`row` は Z 方向。
- `channel` は場の種類。1 メッセージに 1 チャネル。

| channel | 意味 | 書かれる条件 | 半減期 |
|---|---|---|---|
| 0 | 餌痕 | 餌（`k == 2`）に触れている間 | 11 秒 |
| 1 | 通行痕 | 常時 | 140 秒 |
| 2 | 被弾痕 | セルの `h` が正の tick | 20 秒 |
| 3 | 死痕 | `state: dead` かつ `reason: shot` の位置に 1 回 | 120 秒 |

- デバッグ表示専用。Body はこれを行動判断に使わない。省略可能。
- 4 チャネルを 0.25 秒ごとに 1 つずつ順番に送るので、各チャネルは 1 秒に 1 回更新される。

チャネルの追加は後方互換なので `v` は上げない。0 と 1 の意味は変わらない。受信側は知らない `channel` を無視してよい。

### 6.6 `bye`（双方向）

```json
{ "v": 1, "type": "bye", "session": "3f2a...", "tick": 9000, "reason": "mission_end" }
```

Body はミッション終了時に送る。Brain はこれを受けたらログを閉じる。Brain 終了時にも送る。

### 6.7 `error`（双方向）

```json
{ "v": 1, "type": "error", "session": "3f2a...", "tick": 0, "code": "version_mismatch", "message": "expected v=1, got 2" }
```

`code`: `version_mismatch` / `unknown_session` / `malformed` / `too_many_creatures`

## 7. 生存責任の分担

| 事象 | 判断する側 | 通知 |
|---|---|---|
| 出現 | Body | `observe` に `state: spawned` |
| 被弾で HP 0 | Body | `observe` に `state: dead, reason: shot` |
| 餓死 | Brain | `command` に `starved: true` → Body が次 tick で `dead, reason: starved` |
| 捕食（プレイヤー・敵機・残骸への被害） | Body | `eating: true` を observe で通知。Brain はこれでエネルギー回復。食べ尽くされた敵機はプレイヤーの撃破数に数えない |
| ミッション終了 | Body | `bye` |

## 8. タイミング

- Body は毎 `Update` で `observe` を送る（30 Hz 目標、上限 0.1 秒 dt）。
- Brain は `observe` を受けたら同 tick で `command` を返す。決定周期 0.25 秒の間は前回の決定を繰り返し返してよい。
- Brain の応答が遅れても Body は待たない。`command` は最新のものだけ有効。
- ログは Brain 側で `observe`/`command` の両方を tick 単位で記録する（`learning-monster-design.md` §5）。

## 9. サイズ見積

`observe`: 個体 1 体あたりセル 16 × 約 24 バイト ≒ 400 バイト、12 体で約 5 KB。`field`: 64×64 の base64 で約 5.5 KB。いずれも 60,000 バイト上限内。

## 10. 適合テスト

両側に置く固定サンプル `docs/creature-protocol-samples/*.json` を、双方のパーサが受理し、同じ内容を再生成できることをテストする。

- Python: `tests/test_protocol.py` がサンプルを読み込み、`hello` → `welcome`、`observe` → `command` を生成して型・範囲を検証
- Unity: EditMode テスト `CreatureProtocolTests` が `JsonUtility` でサンプルを往復させ、フィールド欠落なしを検証

## 11. 版更新の規則

- 後方互換な追加（フィールド追加）は `v` を上げず、受信側は未知フィールドを無視する。
- 意味変更・削除・セル数変更は `v` を上げる。
- 変更は必ず両リポジトリの同名ファイルに同時に反映する。
