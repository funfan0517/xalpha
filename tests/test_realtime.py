import xalpha as xa
import pandas as pd
import pytest

# 天天基金 fundgz.1234567.com.cn 实时估值接口已下线（返回东方财富 404 页面），
# rtdata 正则匹配失败抛 TypeError。realtime 模块本身已标记 deprecated，暂不修复。


@pytest.mark.skip(reason="fundgz realtime API retired, realtime.py deprecated")
def test_rfundinfo():
    gf = xa.rfundinfo("001469")
    gf.info()
    assert gf.code == "001469"


@pytest.mark.skip(reason="fundgz realtime API retired, realtime.py deprecated")
def test_review(capsys):
    gf = xa.rfundinfo("001469")
    st1 = xa.policy.buyandhold(gf, start="2018-08-10", end="2019-01-01")
    st2 = xa.policy.scheduled_tune(
        gf,
        totmoney=1000,
        times=pd.date_range("2018-01-01", "2019-01-01", freq="W-MON"),
        piece=[(0.1, 2), (0.15, 1)],
    )
    check = xa.review([st1, st2], ["Plan A", "Plan Z"])
    assert isinstance(check.content, str) == True
    conf = {}
    check.notification(conf)
    captured = capsys.readouterr()
    assert captured.out == "没有提醒待发送\n"
    check.content = "a\nb"
    check.notification(conf)
    captured = capsys.readouterr()
    assert captured.out == "邮件发送失败\n"
