"""Isolated request worker: only known public read-only APIs are callable."""
import json
import sys

ALLOWED = {"tool_trade_date_hist_sina", "index_zh_a_hist", "stock_zh_a_hist", "stock_zh_a_spot_em",
           "stock_sector_fund_flow_rank", "stock_zh_a_hist_tx", "stock_zh_index_daily", "stock_zh_index_daily_tx",
           "stock_individual_info_em", "stock_individual_fund_flow", "stock_board_industry_hist_em", "stock_sector_fund_flow_hist"}

def main():
    version = None
    try:
        import akshare as ak
        version = ak.__version__
        request = json.load(sys.stdin)
        if request["function"] not in ALLOWED:
            raise ValueError("unknown API")
        frame = getattr(ak, request["function"])(**request["kwargs"])
        # pandas replaces NaN with null; dates become ISO strings.
        records = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
        print(json.dumps({"records": records, "version": ak.__version__}, ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({"records": [], "error": type(error).__name__, "version": version}))

if __name__ == "__main__":
    main()
