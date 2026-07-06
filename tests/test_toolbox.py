import pytest
import pandas as pd
from pyecharts.charts import Bar
from pyecharts.globals import CurrentConfig, NotebookType
from pyecharts.options import InitOpts
import xalpha as xa
from xalpha.trade import vtradevolume

xa.set_backend(backend="memory", prefix="pytest-")


def test_compare():
    # c = xa.Compare(("FT-JBGOUA:SWX:USD", "USD"), "SH501018", start="20200101")
    # no code can be found for the previous one,
    # seems to me lots of funds cannot be found in FT now
    c = xa.Compare(("FT-GLD:PCQ:USD", "USD"), "SH501018", start="20200101")
    c.corr()
    c.v()


def test_stock_peb():
    h = xa.StockPEBHistory("HK00700")
    h.summary()


def test_overpriced():
    xa.OverPriced("SZ161815", prev=360).v([-1.5, 3.5])


@pytest.mark.local
def test_set_display():
    pytest.importorskip("IPython")
    xa.set_display("notebook")
    df = xa.get_daily("PDD", prev=30)
    df._repr_javascript_()
    xa.set_display()
    assert getattr(pd.DataFrame, "_repr_javascript_", None) is None


def test_set_display_notebook_plus():
    original_type = CurrentConfig.NOTEBOOK_TYPE

    try:
        xa.set_display("notebook+")
        assert CurrentConfig.NOTEBOOK_TYPE == NotebookType.NTERACT
        assert getattr(pd.DataFrame, "_repr_javascript_", None) is None

        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-09"]),
                "cash": [-1000, -500],
            }
        )
        html = vtradevolume(df).data

        assert "<iframe" in html
        assert "require.config" not in html
        assert "echarts.min.js" in html
        assert "dataTables" in df._repr_html_()

        tall_chart = (
            Bar(init_opts=InitOpts(height=500)).add_xaxis(["a"]).add_yaxis("s", [1])
        )
        assert "height:500px" in tall_chart.render_notebook().data

        big_df = pd.DataFrame({"a": range(250)})
        big_html = big_df._repr_html_()
        assert "Showing first 200 of 250 rows." in big_html
        assert ">249<" not in big_html
    finally:
        xa.set_display()

    assert CurrentConfig.NOTEBOOK_TYPE == original_type


@pytest.mark.local
def test_get_currency():
    assert (
        xa.toolbox.get_currency_code("indices/india-50-futures") == "currencies/inr-cny"
    )
    assert xa.toolbox._get_currency_code("JPY") == "100JPY/CNY"


@pytest.mark.skip(reason="cninvesting server check")
def test_qdii_predict():
    hb = xa.QDIIPredict(
        "SZ162411",
        t1dict={".SPSIOP": 91},
        t0dict={
            "commodities/brent-oil": 40 * 0.9,
            "commodities/crude-oil": 60 * 0.9,
        },
        positions=True,
    )
    hb.get_t1()
    hb.get_t0(percent=True)
    hb.benchmark_test("20200202", "20200302")
    hb.analyse()


# @pytest.mark.local
@pytest.mark.skip(reason="cninvesting server check")
def test_qdii_predict_local():
    xc = xa.QDIIPredict("SZ165513", positions=True)
    xc.get_t0_rate()


def test_rt_predict():
    p = xa.RTPredict("SH512500", t0dict="SH000905")
    p.get_t0_rate()


@pytest.mark.local
def test_cbcaculator():
    c = xa.CBCalculator("SH113577")
    d = c.analyse()
    assert d["name"] == "春秋转债"
    # obtain correct redeem_price from superscipt
    c = xa.CBCalculator("SH113604")
    d = c.analyse()
    assert d["name"] == "多伦转债"
