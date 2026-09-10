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

### 5.2 流れ（人工生物だけに効く）

アリーナ全体を回る水の流れ。**人工生物だけ**を運び、自機・敵機・残骸には効かない。計算は Body（`CreatureCurrent.cs`）と Brain の再生シミュレータ（`creature_sim/current.py`）で同一。方策の入力にはしない（流れを感じるかどうかは、今は触覚と実際の移動を通してのみ）。

- `u = (x − minX) / W`、`v = (z − minZ) / H`、`p = 2π t / currentPeriod`、`q = 2π t / (1.618 × currentPeriod)`（`t` はセッション開始からの秒）
- 流れ関数 `ψ = A [ sin(πu) sin(πv) + e sin(p) sin(2πu) sin(πv) + e cos(p) sin(πu) sin(2πv) + m sin(q) sin(2πu) sin(2πv) ]`、`A = currentSpeed × min(W, H) / π`、`e = currentWobble`、`m = currentMix`
- 速度 `vx = ∂ψ/∂z`、`vz = −∂ψ/∂x`

性質:

- 流れ関数から作るので**湧き出しも吸い込みもない**（発散 0）。流れだけでは生物がどこにも集まらない
- アリーナの縁で縁に垂直な成分が 0。壁に押し付けず、壁沿いに流れる
- `e = 0` だと中心が止まった一つの渦。`e > 0` で二つの小さな模様が `currentPeriod` 秒周期で入れ替わり、渦の中心が動き回る
- `e` だけでは、浮かんだものは自分の輪の上を回り続ける（中心から出たものは内側 4 区画に留まる）。`m > 0` の 4 区画の模様が 1.618 倍の周期で出入りし、二つの周期が同じ並びを繰り返さないため、内側と外側の輪の間で入れ替わってアリーナ全体を回遊する（既定 `e = 0.4, m = 0.5` で 900 秒の間に、どの出発点も 16 区画中 13 区画以上を通過。計算で確認）

**速い帯（lane）**: 渦に重ねて、中心から `laneRadius`（0 中心 〜 1 壁の中点）の輪の上に幅 `laneWidth` の一方向の帯を置く。

- `r = 2 √((u − ½)² + (v − ½)²)`（壁の中点で 1）
- `ψ_lane = −B tanh((r − laneRadius) / laneWidth)`、`B = laneSpeed × laneWidth × min(W, H) / 2`
- 帯の中央で速さ `laneSpeed`、渦と同じ向き（負なら逆向き）。帯から幅の数倍離れるとほぼ 0
- 壁を横切る成分は `sech²((1 − laneRadius) / laneWidth) × laneSpeed`。既定値で 1 万分の 3 で、実用上 0

検算（`laneSpeed` 2、`laneRadius` 0.65、`laneWidth` 0.08、渦なし）:

| 位置 (x, z) | r | 流れ (vx, vz) |
|---|---|---|
| (0, −26) | 0.65 | (2, 0) |
| (26, 0) | 0.65 | (0, 2) |
| (0, 26) | 0.65 | (−2, 0) |
| (0, −18.4) | 0.46 | (0.066, 0) |
| (0, 0) | 0 | (0, 0) |

Body は生物自身の移動を済ませて `speed` を確定した後、流れの分を**別の移動**として加える（壁や障害物では止まる）。流された分は `speed` に含めない（Brain が流された分のエネルギーを請求しないため）。大腸菌もクラゲも同じ流れに流される。

検算（アリーナ `[-40,-40,40,40]`、`currentSpeed` 1、`currentPeriod` 90）:

| 位置 (x, z) | t | e | 流れ (vx, vz) |
|---|---|---|---|
| (0, −40) 南の縁 | 0 | 0 | (1, 0) |
| (40, 0) 東の縁 | 0 | 0 | (0, 1) |
| (0, 40) 北の縁 | 0 | 0 | (−1, 0) |
| (−40, 0) 西の縁 | 0 | 0 | (0, −1) |
| (0, 0) 中心 | 0 | 0 | (0, 0) |
| (0, 0) 中心 | 0 | 0.4 | (−0.8, 0) |
| (0, −40) 南の縁 | 0 | 0.4 | (1.8, 0) |
| (0, 40) 北の縁 | 0 | 0.4 | (−0.2, 0) |
| (0, 0) 中心 | 22.5（1/4 周期） | 0.4 | (0, 0.8) |
| (20, 0) | 36.405（`q` の 1/4 周期） | 0、`m` 0 | (0, 0.7071) |
| (20, 0) | 36.405 | 0、`m` 0.5 | (1.0, 0.7071) |

`t = 0` と中心では `m` の項が 0 なので、上の表の他の値は `m` によらない。

上から見て反時計回り（南の縁が +X、東の縁が +Z へ流れる）。

## 6. メッセージ

### 6.1 `hello`（Body → Brain、接続開始と再接続時）

```json
{
  "v": 1, "type": "hello", "session": "3f2a...", "tick": 0,
  "build": "0.5.0-creature",
  "hz": 30,
  "body": { "length": 3.0, "radius": 0.8, "cellRadius": 0.4, "cells": 16, "runSpeed": 8.0, "maxHealth": 120 },
  "jelly": { "runSpeed": 3.0, "maxHealth": 20, "capacity": 16 },
  "capacity": 32,
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

- `body` は大腸菌の身体。`runSpeed` は `command.speed = 1.0` が意味する上限で、個体の速度遺伝子はこれを下回る比率で効く。
- `jelly`（任意）: クラゲの身体。皮膚の幾何（長さ・半径・セル）は大腸菌と同じで、速度と HP だけ違う。`capacity` はクラゲのプール数。トップレベルの `capacity` は両種の合計（個体数の上限。分裂はこれを超えない）。
- `arena.bounds` は `[minX, minZ, maxX, maxZ]`。スティグマジー場はこの矩形に貼る。
- `arena.obstacles` は Brain の**再生シミュレータ用**。リアルタイム推論には使わない（生物は目が見えない）。
- `pilot`（任意）: `{ "kind": "human" | "autopilot", "seed": 7, "sortie": 2, "damageScale": 0.1 }`。`damageScale` は自機が受けるダメージの倍率（1 通常 / 0.1 で約 10 倍タフ / 0 で無敵、切り上げ）。自動出撃のときだけ 1 以外になる。誰が操縦したセッションかをログで区別するため。方策の入力には使わない。
- `infestation`（任意）: `{ "wreckBites": 10, "mechSeconds": 8, "maxMechs": 2, "wreckOnKill": true, "currentSpeed": 1, "currentPeriod": 90, "currentWobble": 0.4, "currentMix": 0.5, "laneSpeed": 2, "laneRadius": 0.65, "laneWidth": 0.08, "oneShotKills": true, "mechBites": 10 }`（`mechBites` は生きた敵機を食べ尽くす口数）。そのセッションのバランス設定。再生シミュレータを本編に合わせるため。`current*` と `lane*` は §5.2 の流れ（`currentSpeed` 0 または欠落で渦なし、`laneSpeed` 0 で帯なし）。
- 無人の自動出撃（`-auto-sorties`）では、Body は各 tick の `observe` を送った後、その tick の `command` が届くまで待つ（lockstep、既定 250 ms で打ち切り）。ゲーム時間は実時間より速く進むが、Brain 側の処理は変わらない。人が操縦するときは待たない。
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
      "species": "ecoli",
      "state": "alive",
      "bite": "mech",
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
| `player` | **Brain のログと学習にのみ使う。方策の入力に入れてはならない**。自機を幽霊にした無人出撃（`-auto-pilot-off`）では**省略される**。Brain と観測画面は「自機なし」として扱う |
| `creatures[].id` | Body が付与する 0 以上の整数。プール再利用時も同じ id を使ってよいが、`state` が `spawned` の tick で Brain は個体状態をリセットする |
| `species` | `ecoli` / `jelly`。省略時 `ecoli`。Brain は種ごとに別のコロニーで扱う |
| `state` | `spawned`（この tick で出現）/ `alive` / `dead`（この tick で死亡。次 tick から一覧に含めない） |
| `parent` | `state == spawned` で、`command.divide` による分裂で生まれた個体にだけ付く親の id。Brain はこれを見て遺伝子を継がせ、親のエネルギーを分ける（§7）。分裂以外の出現には付かない |
| `reason` | `state == dead` のときのみ。`shot`（被弾）/ `starved`（Brain 指示）/ `eaten`（大腸菌に食べ尽くされたクラゲ）/ `despawn`（ミッション終了など） |
| `bite` | この tick に噛みついた相手: `player` / `mech` / `wreck` / `jelly`。噛んだ tick にだけ付く。Brain の栄養計算に使う（相手で栄養価が違う）。クラゲは何も噛まないので付かない |
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
    { "id": 5, "mode": "idle",   "speed": 0.0, "turn": 0.0,  "deposit": 0.0, "energy": 0.0,  "starved": true, "divide": false },
    { "id": 6, "mode": "run",    "speed": 0.7, "turn": 0.0,  "deposit": 0.0, "energy": 0.9,  "starved": false, "divide": true }
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
| `ring` / `rest` / `drift` | 任意 | | クラゲにだけ付く観測画面用の付録: 神経リング 8 セルの興奮（0..1）、不応期の残り tick、蓄積した推進の向き `[vx, vz]`。**Body は無視する** |
| `divide` | bool | | true なら Body は同じ種の子を隣に出現させる（`observe` に `state: spawned, parent: id` で現れる）。プールが満杯なら何も起きず、Brain は子が現れないことでそれを知る。省略時 false |

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
| 4 | プランクトン | 残骸（`prey` の `wreck`）の位置に毎 tick 少しずつ（セル上限あり）。クラゲが食べて減る | 90 秒 |

- デバッグ表示専用。Body はこれを行動判断に使わない。省略可能。
- 5 チャネルを 0.25 秒ごとに 1 つずつ順番に送るので、各チャネルは 1.25 秒に 1 回更新される。
- チャネル 4 だけは痕跡ではなく餌。大腸菌はこれを食べない（残骸そのものを噛む）。クラゲはこれしか食べない。

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
| 捕食（プレイヤー・敵機・残骸・クラゲへの被害） | Body | 噛んだ tick に `bite` を observe で通知。Brain は相手ごとの栄養価でエネルギー回復（敵機は 2 口で満腹、残骸は 10 口以上）。食べ尽くされた敵機はプレイヤーの撃破数に数えない |
| クラゲの摂食 | Brain | プランクトン場（チャネル 4）を自分の位置で食べる。Body は関与しない |
| 分裂 | Brain が判断、Body が実行 | 満腹（遺伝子の閾値以上）が続くと `command.divide`。Body が子を隣に出し `parent` 付きで報告。Brain は子に変異した遺伝子と親のエネルギーの半分を与える |
| 被弾で即死 | Body | `hello.infestation.oneShotKills` が true なら、自機・敵機どちらの弾でも 1 発で `dead, reason: shot`。プレイヤーの撃破数に入るのは自機の弾だけ |
| 敵機の狙い | Body | 敵機は自機を狙うが、自機より近い生物、または自機が射程外・視界外のときは 30 m 以内の生物を撃つ |
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
