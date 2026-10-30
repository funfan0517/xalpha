# -*- coding: utf-8 -*-
"""波动资产 · V3 变种：等金额网格（逐腿）—— 规则与引擎。

规则卡（README §1 逐条镜像，可直接转代码）：
1. 配置：中间价 P0 / 格距 `grid_pct=3%` / 总格数 `N=10` / 初始买入 `N//2` 份 / **每份金额相等**；
2. 价格线：`P0 × (1 ± k×grid_pct)`，k = 1..N//2（百分比网格；固定价差版见 `lines(mode="abs")`）；
3. 初始：现金 = 总资金、持仓 0 → 先买 `init_units` 份；
4. 触发：跌破下一格 → 买 1 份（现金够且未买满）；涨破上一格 → 卖 1 份（有持仓且未卖光）；
5. 出界：**卖光后不追高**（要继续必须换 P0 重画网格）；**买满后不再追加新资金**；
6. 风控：开格前做「买满后再跌 36%」压力测试，扛不住就**减少总资金**（`stress()`）；
7. 成本：每次成交扣手续费 / 滑点（格子越小摩擦越重）。

与主策略（v1.3 仓位带）的关系：
- 池 = `rule.LEG_ORDER` 六腿，价格 = `data.series_for_leg()` 场外**复权净值**（与 v1_ma20 同源同口径）；
- 成交纪律 = T 日盘后按收盘净值判档、**T+1 净值成交**（`LAG=1`），与 vol_legs §2.4 同一纪律；
- 未投现金按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息。
- 三者可叠加：主策略定「组合层仓位带」→ 本变种定「带内按格子高抛低吸」→ v1_ma20 定「何时整腿下车」。

## 对「极简伪代码」的三处必要修正（否则算不出净值 / 会重复触发）

① **记账**：伪代码 `buy/sell` 只动 `cash ±n*unit`、**完全不看价格**，无法折算净值。本实现改为
   **逐批份额记账**：每份以成交价换成份额（`shares = unit/price`，等金额买入 = 「每份金额相等」），
   卖出时卖**最近一次买入的批次**（LIFO = 最低价那批），于是每次「低一格买、回一格卖」的毛利
   就是 `unit × 格距`，剩余份额即已实现收益。
② **判档**：伪代码用 `round()` 且每根 bar 只走 1 份，价格在格线附近抖动会来回触发。本实现用
   **「已跨过几条线」的整数档位 + 只在新档位成交**（`level()`，向零取整，档位恰在价格线上跳变），
   `MAX_UNITS_PER_BAR` 限制单日最多补 1 格（忠实规则卡），并保留 `cooldown` 冷却；
   成交口径是**收盘净值**（不逐 tick）。
③ **出界**：伪代码对「卖光 / 买满」只 `return` 一个字符串，没有出路。本实现：
   - 卖光 → 状态冻结（**不再买**，绝不追高），只有 `reanchor` 允许的动作能重开：`period`（每 N 个
     交易日以当日收盘重画 P0，保留持仓）或 `pullback`（从卖光后高点回落 `reanchor_drop` 再重画并
     按规则卡重新买入 `init_units` 份）；
   - 买满 → **不注入新资金**（类里根本没有加钱的方法），但价格回升跨格时照常卖出释放份额。
"""
import math
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                        # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))       # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V3 等金额网格"
LEGS = rule.LEG_ORDER
# 组合层权重 = 目标带归一化（与 v1_ma20 同一套：Σ=1，满仓 = 40pp）
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

# ---------------------------------------------------------------- 1. 参数（规则卡 §1）
# 改参数只改这里；回测 / 每日扫描都用同名默认值。
GRID_PCT = 0.03          # 格距（等比例网格）；规则卡演示值 3%
TOTAL_UNITS = 10         # 总格数 N；规则卡演示值 10（上/下各 5 格）
INIT_FRAC = 0.5          # 初始买入 = N/2
MAX_UNITS_PER_BAR = 1    # 单日最多补/减几份（1 = 忠实规则卡「一次一格」）
COOLDOWN_BARS = 0        # 两次成交之间至少间隔的交易日数（0 = 不冷却）
LAG = 1                  # 成交滞后：T 日盘后判档 → T+1 净值成交
# LAG=1 时的挂单处理：「限价」= 次日净值须仍在格线外侧才成交，回到内侧则撤单
# （场外只能按当日净值成交，价格已越过格线就不该再按旧档位补票）；
# "market" = 不看价格一律按次日净值成交 —— 会产生「同一价位买了又卖」的纯摩擦，仅作对照。
FILL_RULE = "limit"

# 出界后的出路（规则卡 §5「要继续就换 P0 重画网格」）
#   "off"      = 不重画：与规则卡原样一致（卖光即停 / 买满即套），回测里用它看代价
#   "period"   = 每 REANCHOR_BARS 个交易日以当日收盘重画 P0（保留持仓，档位归零）
#   "pullback" = 卖光后**必须**跌破卖光价的 (1−REANCHOR_DROP) 才重画并重买 init 份
#                （「不追高」的硬实现：绝不高于卖光价重开）
#   "both"     = 两者都开（默认）
#   "rebase"   = 卖光后立刻以当日价重画并重买 init 份 —— **就是追高**，
#                规则卡明令禁止，只作「追高的代价/收益」对照口径
REANCHOR = "both"
REANCHOR_BARS = 250      # 约一年
REANCHOR_DROP = 0.10     # 约 3 格（3% 格距）
REANCHOR_REBUY = True    # 卖光重画后是否按规则卡重新买入 init 份

SLIP = 0.0               # 滑点（成交价单边劣化）。场外按净值成交 → 默认 0；场内口径可给 0.001
STRESS_DROP = 0.36       # 规则卡 §6：买满后再跌 36%

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1_ma20/FEE 同一张表：
# A 类：申购 0.10% + 赎回 0.50%（持有 >7 天）；C 类申赎免费，另收约 0.2%/年销售服务费（**未计**）。
FEES = {
    "a500": (0.0, 0.0),
    "ndx": (rule.FEE_BUY, rule.FEE_SELL),
    "spx": (rule.FEE_BUY, rule.FEE_SELL),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (rule.FEE_BUY, rule.FEE_SELL),
}


def fees(leg):
    """该腿单边费率 (买入, 卖出)。"""
    return FEES.get(leg, (float(rule.FEE_BUY), float(rule.FEE_SELL)))


# ---------------------------------------------------------------- 2. 引擎
class GridTrader:
    """等金额网格账户：一份 = `unit` 钱，逐批份额记账。

    与伪代码的对应关系：`p0 / grid_pct / total_units / unit / cash / init_units` 同名同义；
    `holdings` 从「份」的计数改为 `len(lots)`（仍是一份一份地加减），`last_price` 改为 `last_k`
    （档位而非价格，避免格线附近的浮点抖动）。
    """

    def __init__(self, p0, capital=1.0, grid_pct=None, total_units=None, init_frac=None,
                 fee=(0.0, 0.0), slip=None, cash_daily=None, reanchor=None,
                 reanchor_bars=None, reanchor_drop=None, reanchor_rebuy=None,
                 max_units_per_bar=None, cooldown=None):
        self.grid_pct = float(GRID_PCT if grid_pct is None else grid_pct)
        self.total_units = int(TOTAL_UNITS if total_units is None else total_units)
        self.init_frac = float(INIT_FRAC if init_frac is None else init_frac)
        self.init_units = int(round(self.total_units * self.init_frac))
        self.capital = float(capital)
        self.unit = self.capital / self.total_units        # 每份金额（等金额网格）
        self.cash = float(capital)
        self.lots = []                                    # [(成交价, 份额)]，后进先出
        self.fee_buy, self.fee_sell = float(fee[0]), float(fee[1])
        self.slip = float(SLIP if slip is None else slip)
        self.cash_daily = float(CASH_DAILY if cash_daily is None else cash_daily)
        self.reanchor = REANCHOR if reanchor is None else str(reanchor)
        self.reanchor_bars = int(REANCHOR_BARS if reanchor_bars is None
                                 else (reanchor_bars or 0))
        self.reanchor_drop = float(REANCHOR_DROP if reanchor_drop is None else reanchor_drop)
        self.reanchor_rebuy = bool(REANCHOR_REBUY if reanchor_rebuy is None else reanchor_rebuy)
        self.max_per_bar = (MAX_UNITS_PER_BAR if max_units_per_bar is None
                            else max_units_per_bar)
        self.cooldown = int(COOLDOWN_BARS if cooldown is None else cooldown)

        self.p0 = float(p0)
        self.last_k = 0
        self.current_price = float(p0)
        self.bars = 0
        self.last_fill_bar = -10 ** 9
        self.days_sold_out = 0
        self.days_full = 0
        self.sold_out_at = None        # 卖光那一刻的成交价（「不追高」基准）
        self.fills = []
        self.reanchors = []
        self.realized = 0.0            # 累计已实现（份额卖出 - 买入成本）
        self.fee_total = 0.0
        self.turnover = 0.0            # 累计成交额 / 初始资金
        self.last_act = None
        self.last_act_bar = -1         # last_act 是在第几根 bar 判出来的（每日报告判新旧用）

        self.buy(self.p0, self.init_units, tag="init")      # 规则卡 §3：先买一半

    # -------------------------------------------------- 网格几何
    def level(self, price):
        """价格**已经越过几条格线**：+k 向上、-k 向下，0 = 还在 P0 附近、未越线。

        取整方式必须**向零取整**（`int(x)`），原因有三：
          - `floor` 会把 0→-1 的跳变放在 **P0 本身**，于是「跌一格买 1 份」变成「跌破 P0 就买」，
            每一次成交都比格线早整整一格（早期版本踩过这个坑）；
          - `round`（伪代码的写法）在 ±0.5 格处跳变，价格在格线附近抖动会反复成交同一格；
          - 向零取整使档位**恰好**在价格线 `P0×(1±k·grid_pct)` 上跳变，与规则卡 §2 的价格线定义一致。

        返回的是档位而不是价格，所以 `last_k` 的比较天然免疫浮点抖动。
        """
        x = (float(price) / self.p0 - 1.0) / self.grid_pct
        return int(x + (1e-9 if x >= 0 else -1e-9))

    def lines(self, mode="pct"):
        """完整价格线（下 N/2 … 上 N/2），含中间价 P0。"""
        half = self.total_units // 2
        out = []
        for k in range(-half, half + 1):
            px = (self.p0 * (1.0 + k * self.grid_pct) if mode == "pct"
                  else self.p0 + k * self.p0 * self.grid_pct)
            out.append((k, px))
        return out

    def next_levels(self, price=None):
        """下一格买价 / 上一格卖价（每日报告用）。"""
        price = self.current_price if price is None else float(price)
        k = self.level(price)
        return (self.p0 * (1.0 + (k - 1) * self.grid_pct),
                self.p0 * (1.0 + (k + 1) * self.grid_pct), k)

    # -------------------------------------------------- 账户状态
    def holdings(self):
        return len(self.lots)

    def shares(self):
        return sum(s for _p, s in self.lots)

    def invested(self, price):
        return self.shares() * float(price)

    def nav(self, price):
        return self.cash + self.invested(price)

    def affordable(self):
        """现金还能买几份（含买入费）。"""
        if self.unit <= 0:
            return 0
        return max(0, int(self.cash / (self.unit * (1.0 + self.fee_buy)) + 1e-12))

    def state(self):
        """ok / sold_out / buy_full（规则卡 §5 的两种出界）。"""
        if self.holdings() <= 0:
            return "sold_out"
        if self.holdings() >= self.total_units:
            return "buy_full"
        return "ok"

    # -------------------------------------------------- 成交
    def buy(self, price, n=1, date=None, tag="", level=None):
        """按等金额买入 n 份；返回实际成交份数（受现金与未买满约束）。"""
        n = int(n)
        if n <= 0:
            return 0
        n = min(n, self.total_units - self.holdings(), self.affordable())
        if n <= 0:
            return 0
        px = float(price) * (1.0 + self.slip)
        amount = n * self.unit
        fee = amount * self.fee_buy
        self.cash -= amount + fee
        per = amount / px / n                       # 每份份额（等金额买入）
        for _ in range(n):
            self.lots.append((px, per))
        self.fee_total += fee
        self.turnover += amount / self.capital
        self._log(date, "buy", n, px, amount, fee, 0.0, tag, level)
        return n

    def sell(self, price, n=1, date=None, tag="", level=None):
        """卖出 n 份（后进先出 = 卖最近/最低价买入的那批）；返回实际成交份数。"""
        n = int(n)
        if n <= 0:
            return 0
        n = min(n, self.holdings())
        if n <= 0:
            return 0
        px = float(price) * (1.0 - self.slip)
        amount = fee = 0.0
        for _ in range(n):
            _cost_px, sh = self.lots.pop()
            amt = sh * px
            amount += amt
            fee += amt * self.fee_sell
        self.cash += amount - fee
        realized = amount - fee - n * self.unit      # 每份成本恒为 unit（等金额）
        self.realized += realized
        self.fee_total += fee
        self.turnover += amount / self.capital
        if not self.lots:                            # 刚刚卖光 → 记下成交价，作为「不追高」的基准
            self.sold_out_at = px
        self._log(date, "sell", n, px, amount, fee, realized, tag, level)
        return n

    def _log(self, date, side, n, px, amount, fee, realized, tag, level=None):
        self.fills.append(dict(
            date=(str(date.date()) if hasattr(date, "date") else str(date)),
            side=side, n=int(n), price=float(px),
            level=(self.level(px) if level is None else int(level)),
            amount=float(amount), fee=float(fee), realized=float(realized),
            holdings=self.holdings(), cash=float(self.cash), tag=tag))
        self.last_fill_bar = self.bars

    # -------------------------------------------------- 出界与重画
    def reanchor_to(self, price, date=None, why="", rebuy=None):
        """换 P0 重画网格（规则卡 §5）。rebuy=False 时保留现有持仓、档位归零。"""
        self.p0 = float(price)
        self.last_k = 0
        self.reanchors.append(dict(
            date=(str(date.date()) if hasattr(date, "date") else str(date)),
            p0=self.p0, why=why, holdings=self.holdings(), cash=float(self.cash)))
        flag = self.reanchor_rebuy if rebuy is None else bool(rebuy)
        if flag and self.holdings() == 0:
            self.buy(self.p0, self.init_units, date=date, tag="reanchor")

    def revive_price(self):
        """卖光后允许重画（重开网格）的价格上限 = 最后一次卖价的 (1 − reanchor_drop)。

        规则卡 §5「卖光后不追高」的硬实现：**绝不在高于卖光价的位置重开**，
        必须等价格回落到卖光价下方 `reanchor_drop` 才换 P0 重画。
        """
        if self.sold_out_at is None or self.reanchor_drop <= 0:
            return None
        return self.sold_out_at * (1.0 - self.reanchor_drop)

    def _pullback_hit(self, price):
        if self.reanchor not in ("pullback", "both"):
            return False
        cap = self.revive_price()
        return cap is not None and float(price) <= cap

    # -------------------------------------------------- 主循环
    def decide(self, price, i=0, date=None):
        """按本日收盘判档，返回动作 dict 或 None（成交由 execute 负责，便于走 T+1）。"""
        act = self._decide(price, i, date)
        self.last_act, self.last_act_bar = act, i
        return act

    def _decide(self, price, i=0, date=None):
        """判档本体：绝不在这里买/卖，只改 `last_k` 与 p0 —— 这样「判档」与「成交」能隔一天。"""
        self.bars += 1
        price = float(price)

        # 卖光：不追高（不在高于卖光价的位置重开），只有重画能把网格救活
        if self.holdings() <= 0:
            self.days_sold_out += 1
            if self.reanchor == "rebase":
                self.reanchor_to(price, date, why="卖光后立即重画(追高对照)")
                act = dict(side="reanchor", n=self.init_units, price=price, level=0,
                           reason="sold_out_rebase")
                return act
            if self._pullback_hit(price):
                self.reanchor_to(price, date, why="卖光后回落重画")
                return dict(side="reanchor", n=self.init_units, price=price, level=0,
                            reason="sold_out_pullback")
            return None

        if self.holdings() >= self.total_units:
            self.days_full += 1

        # 定期重画（walk-forward：长期单边趋势下静态网格会死在半路）
        if (self.reanchor in ("period", "both") and self.reanchor_bars > 0
                and i > 0 and i % self.reanchor_bars == 0):
            self.reanchor_to(price, date, why="定期重画", rebuy=False)
            return dict(side="reanchor", n=0, price=price, level=0, reason="period")

        k = self.level(price)
        d = k - self.last_k
        self.last_k = k
        if d == 0:
            return None
        if self.bars - self.last_fill_bar < self.cooldown:      # 冷却期：档位照走，不成交
            return None
        steps = abs(d)
        cap = steps if self.max_per_bar in (None, 0) else min(steps, int(self.max_per_bar))
        if d < 0:
            n = min(cap, self.total_units - self.holdings(), self.affordable())
            return dict(side="buy", n=int(n), price=price, level=k) if n > 0 else None
        n = min(cap, self.holdings())
        return dict(side="sell", n=int(n), price=price, level=k) if n > 0 else None

    def execute(self, act, price, date=None):
        """按成交日价格执行 decide 的动作。"""
        if not act:
            return None
        if act["side"] == "reanchor":
            if int(act.get("n") or 0) > 0:
                self.buy(price, int(act["n"]), date=date, tag="reanchor", level=0)
            return act
        n = (self.buy(price, act["n"], date=date, level=act.get("level"))
             if act["side"] == "buy"
             else self.sell(price, act["n"], date=date, level=act.get("level")))
        return dict(act, filled=int(n))


def run(px, p0=None, capital=1.0, grid_pct=None, total_units=None, init_frac=None,
        fee=None, slip=None, cash_daily=None, lag=None, reanchor=None,
        reanchor_bars=None, reanchor_drop=None, reanchor_rebuy=None,
        max_units_per_bar=None, cooldown=None):
    """逐日复算一条腿的网格净值。-> dict(nav, trader, exposure, fills, ...)

    p0 缺省 = 首根收盘价（「开格日」由样本起点决定，见 README §6 的起始点风险）。
    lag=1（默认）表示 T 日盘后判档、T+1 净值成交；初始买入在 p0 当日完成（规则卡 §3）。
    """
    px = px.dropna().astype("float64").sort_index()
    if len(px) < 2:
        raise ValueError("样本太短（< 2 根）")
    lag = LAG if lag is None else int(lag)
    p0 = float(px.iloc[0]) if p0 is None else float(p0)
    tr = GridTrader(p0=p0, capital=capital, grid_pct=grid_pct, total_units=total_units,
                    init_frac=init_frac, fee=fee or (0.0, 0.0), slip=slip,
                    cash_daily=cash_daily, reanchor=reanchor, reanchor_bars=reanchor_bars,
                    reanchor_drop=reanchor_drop, reanchor_rebuy=reanchor_rebuy,
                    max_units_per_bar=max_units_per_bar, cooldown=cooldown)
    navs, pend, expo = [], None, []
    for i, (d, price) in enumerate(px.items()):
        tr.cash *= (1.0 + tr.cash_daily)                    # 未投现金按货基计息
        act = None
        if pend is not None:
            tr.bars += 1
            # 昨日判档、今日成交。FILL_RULE="limit"：今日净值若已回到格线内侧，
            # 这笔挂单视为未成交（场外只能按当日净值成交，不能按昨天的价格补票）。
            k_now = tr.level(price)
            ok = (k_now <= pend["level"]) if pend["side"] == "buy" else (k_now >= pend["level"])
            if ok or FILL_RULE == "market":
                tr.execute(pend, price, d)
            else:
                tr.last_k = k_now           # 挂单失效：档位对齐到今日，避免次日「反手」白交手续费
            pend = None
        else:
            act = tr.decide(price, i, d)
            if act is not None and int(act.get("n") or 0) > 0:
                if lag > 0:
                    pend = act
                else:
                    tr.execute(act, price, d)
        tr.current_price = float(price)
        v = tr.nav(price)
        navs.append(v)
        expo.append(tr.invested(price) / v if v > 0 else 0.0)
    nav = pd.Series(navs, index=px.index, dtype="float64")
    return dict(nav=nav, trader=tr, fills=tr.fills, reanchors=tr.reanchors,
                exposure=float(sum(expo) / len(expo)), days_sold_out=tr.days_sold_out,
                days_full=tr.days_full, realized=tr.realized, fee_total=tr.fee_total,
                turnover=tr.turnover, bars=len(px), p0=p0)


# ---------------------------------------------------------------- 3. 风控（规则卡 §6）
def stress(p0, grid_pct=None, total_units=None, init_frac=None, capital=1.0,
           drop=None, fee_buy=None):
    """「买满后再跌 36%」压力测试 —— 开格前必做。

    买满路径：P0 先买 init 份，之后每跌一格补 1 份，直到 N 份（最后一格 = P0×(1-(N-init)×g)），
    再从**最后一格**再跌 `drop`（默认 36%）。返回口径：
      loss_pct = 1 − 期末市值 / (初始资金 + 买入费)，即**该腿额度**的亏损比例。
    若该腿额度 = band pp of 总资产，则对总资产的损失 = loss_pct × band。
    """
    g = float(GRID_PCT if grid_pct is None else grid_pct)
    n = int(TOTAL_UNITS if total_units is None else total_units)
    init = int(round(n * (INIT_FRAC if init_frac is None else init_frac)))
    d = float(STRESS_DROP if drop is None else drop)
    fb = float(rule.FEE_BUY if fee_buy is None else fee_buy)
    unit = float(capital) / n
    lots = [(float(p0), unit / float(p0))] * init                     # 初始一份一半
    for k in range(1, n - init + 1):                                  # 逐格补满
        px = float(p0) * (1.0 - k * g)
        lots.append((px, unit / px))
    bottom = min(p for p, _s in lots)
    worst = bottom * (1.0 - d)
    cost = n * unit
    outlay = cost + cost * fb
    value = sum(sh * worst for _p, sh in lots)
    return dict(p0=float(p0), grid_pct=g, units=n, init_units=init,
                bottom=bottom, worst=worst, drop=d,
                avg_cost=cost / sum(sh for _p, sh in lots),
                value=value, outlay=outlay,
                loss_pct=1.0 - value / outlay,
                loss_amount=(outlay - value))


def stress_budget(loss_pct, tolerable_pp):
    """按可承受亏损反推该腿额度上限（pp of 总资产）。"""
    if loss_pct <= 0:
        return float("inf")
    return float(tolerable_pp) / float(loss_pct)


# ---------------------------------------------------------------- 4. 数据与绩效
def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def perf(nav):
    """绩效指标（复用主策略 engine.metrics：rf = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def trade_stats(fills):
    """成交统计：笔数 / 年笔数 / 卖出胜率 / 每笔已实现 / 费用与换手。"""
    sells = [f for f in fills if f["side"] == "sell"]
    buys = [f for f in fills if f["side"] == "buy"]
    wins = [f["realized"] for f in sells if f["realized"] > 0]
    return dict(n=len(sells), n_buy=len(buys), n_sell=len(sells),
                win_rate=(len(wins) / len(sells)) if sells else 0.0,
                realized=sum(f["realized"] for f in sells),
                fee=sum(f["fee"] for f in fills),
                avg_realized=(sum(f["realized"] for f in sells) / len(sells)) if sells else 0.0)


def common_frame():
    """六腿场外净值的共同窗口（受恒生科技 012805 于 2021-08 成立限制）。"""
    cols = {k: load(k) for k in LEGS}
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, weight=None, fee_by_leg=None, **kw):
    """组合层：Σ w_i × 该腿网格净值（每条腿各占自己的额度，内部现金同利率）。

    Σw = 1（按目标带归一化），所以组合净值 = Σ w_i·nav_i，各腿都从 1.0（= 该腿额度）起算。
    """
    weight = weight or W
    fee_by_leg = fee_by_leg or {}
    navs, detail = {}, {}
    for k in frame.columns:
        r = run(frame[k], fee=fee_by_leg.get(k, (0.0, 0.0)), **kw)
        navs[k] = r["nav"] / r["nav"].iloc[0]
        detail[k] = r
    nav = sum(navs[k] * weight[k] for k in navs)
    return nav, detail


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：格线对称、初始买一半、压力测试、六腿可读。"""
    tr = GridTrader(p0=4724.0, capital=100000.0)
    lines = tr.lines()
    assert len(lines) == TOTAL_UNITS + 1
    assert abs(lines[0][1] - 4724 * (1 - 5 * GRID_PCT)) < 1e-9
    assert tr.holdings() == tr.total_units // 2
    assert abs(tr.cash - 100000.0 * 0.5) < 1e-6
    print(f"格线: {lines[0][1]:.2f} … {lines[-1][1]:.2f}（{len(lines)} 条）· "
          f"初始 {tr.holdings()} 份 / 现金 {tr.cash:.0f} / 每份 {tr.unit:.0f}")
    s = stress(4724.0, capital=100000.0, drop=STRESS_DROP)
    print(f"压力测试: 买满于 {s['bottom']:.2f} → 再跌 {s['drop']:.0%} = {s['worst']:.2f} → "
          f"额度亏 {s['loss_pct']:.1%}（{s['loss_amount']:.0f} 元）")
    for k in LEGS:
        px = load(k)
        r = run(px, fee=fees(k))
        m = perf(r["nav"])
        print(f"  {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 网格年化 {m['ann']:+.2%} 回撤 {m['mdd']:+.2%} "
              f"成交 {len(r['fills']):3d} 笔 曝光 {r['exposure']:.0%} 卖光 {r['days_sold_out']} 日")


if __name__ == "__main__":
    _self_check()
