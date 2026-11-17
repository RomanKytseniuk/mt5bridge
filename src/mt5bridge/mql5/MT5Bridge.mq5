//+------------------------------------------------------------------+
//|                                                    MT5Bridge.mq5 |
//|  Read-only bridge for the mt5bridge CLI and MCP server.          |
//|  Answers requests through files in MQL5\Files\mt5bridge.         |
//|  It never places, modifies or closes orders.                     |
//+------------------------------------------------------------------+
#property copyright   "mt5bridge contributors"
#property version     "1.01"
#property description "Read-only bridge for the mt5bridge CLI and MCP server."
#property description "Answers requests through files in MQL5\\Files\\mt5bridge."
#property description "Never places, modifies or closes orders."

input int InpPollMs  = 100;    // Poll interval for requests, ms
input int InpMaxBars = 20000;  // Max candles per request

#define EA_VERSION    "1.0.1"
#define PROTO_VERSION 1
#define ROOT_DIR      "mt5bridge"
#define REQ_DIR       "mt5bridge\\req"
#define RESP_DIR      "mt5bridge\\resp"
#define MAX_PER_TIMER 32

string g_keys[];
string g_vals[];
int    g_out     = INVALID_HANDLE;
string g_buf     = "";
ulong  g_last_hb = 0;

string          TF_NAMES[] = {"M1","M2","M3","M4","M5","M6","M10","M12","M15","M20","M30",
                              "H1","H2","H3","H4","H6","H8","H12","D1","W1","MN1"};
ENUM_TIMEFRAMES TF_VALS[]  = {PERIOD_M1,PERIOD_M2,PERIOD_M3,PERIOD_M4,PERIOD_M5,PERIOD_M6,
                              PERIOD_M10,PERIOD_M12,PERIOD_M15,PERIOD_M20,PERIOD_M30,
                              PERIOD_H1,PERIOD_H2,PERIOD_H3,PERIOD_H4,PERIOD_H6,PERIOD_H8,
                              PERIOD_H12,PERIOD_D1,PERIOD_W1,PERIOD_MN1};

//+------------------------------------------------------------------+
//| Lifecycle                                                        |
//+------------------------------------------------------------------+
int OnInit()
  {
   FolderCreate(ROOT_DIR);
   FolderCreate(REQ_DIR);
   FolderCreate(RESP_DIR);
   int poll = InpPollMs < 20 ? 20 : InpPollMs;
   if(!EventSetMillisecondTimer(poll))
     {
      PrintFormat("MT5Bridge: cannot start timer, error %d", GetLastError());
      return(INIT_FAILED);
     }
   WriteHeartbeat();
   PrintFormat("MT5Bridge %s started. Folder: %s\\MQL5\\Files\\%s",
               EA_VERSION, TerminalInfoString(TERMINAL_DATA_PATH), ROOT_DIR);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   FileDelete(ROOT_DIR + "\\status.json");
  }

void OnTick()
  {
  }

void OnTimer()
  {
   ProcessRequests();
   ulong now = GetTickCount64();
   if(now - g_last_hb >= 1000)
     {
      WriteHeartbeat();
      g_last_hb = now;
     }
  }

//+------------------------------------------------------------------+
//| Request loop                                                     |
//+------------------------------------------------------------------+
void ProcessRequests()
  {
   string names[];
   int    n = 0;
   string fname;
   long   h = FileFindFirst(REQ_DIR + "\\*.req", fname);
   if(h == INVALID_HANDLE)
      return;
   do
     {
      ArrayResize(names, n + 1);
      names[n++] = fname;
     }
   while(n < MAX_PER_TIMER && FileFindNext(h, fname));
   FileFindClose(h);
   for(int i = 0; i < n; i++)
      HandleRequest(names[i]);
  }

bool ValidId(const string id)
  {
   int len = StringLen(id);
   if(len == 0 || len > 64)
      return(false);
   for(int i = 0; i < len; i++)
     {
      ushort c = StringGetCharacter(id, i);
      bool ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '-' || c == '_';
      if(!ok)
         return(false);
     }
   return(true);
  }

void HandleRequest(const string name)
  {
   string id  = StringSubstr(name, 0, StringLen(name) - 4);
   string src = REQ_DIR + "\\" + name;
   if(!ValidId(id))
     {
      FileDelete(src);
      return;
     }
   // Claim the request: if another bridge instance already moved it, skip.
   string work = REQ_DIR + "\\" + id + ".work";
   if(!FileMove(src, 0, work, 0))
      return;
   string text = ReadAll(work);
   FileDelete(work);
   ParseParams(text);
   string cmd = Param("cmd");

   if(!BeginResponse(id))
      return;
   Out("{" + KV("id", JStr(id)) + "," + KV("cmd", JStr(cmd)) + ",");
   string err = "";
   bool   ok  = Dispatch(cmd, err);
   if(ok)
      Out("," + KV("ok", "true"));
   else
      Out(KV("ok", "false") + "," + KV("error", JStr(err)));
   Out("}");
   EndResponse(id);
  }

bool Dispatch(const string cmd, string &err)
  {
   if(cmd == "ping")
      return(CmdPing(err));
   if(cmd == "account")
      return(CmdAccount(err));
   if(cmd == "deals")
      return(CmdDeals(err));
   if(cmd == "position_deals")
      return(CmdPositionDeals(err));
   if(cmd == "rates")
      return(CmdRates(err));
   if(cmd == "positions")
      return(CmdPositions(err));
   if(cmd == "symbol")
      return(CmdSymbol(err));
   err = "unknown command: " + cmd;
   return(false);
  }

//+------------------------------------------------------------------+
//| Commands. Each one writes "data":{...} only on success.          |
//+------------------------------------------------------------------+
bool CmdPing(string &err)
  {
   Out("\"data\":{");
   Out(KV("proto", JInt(PROTO_VERSION)) + ",");
   Out(KV("ea_version", JStr(EA_VERSION)) + ",");
   Out(KV("terminal_build", JInt(TerminalInfoInteger(TERMINAL_BUILD))) + ",");
   Out(KV("connected", JBool(TerminalInfoInteger(TERMINAL_CONNECTED) != 0)) + ",");
   Out(KV("data_path", JStr(TerminalInfoString(TERMINAL_DATA_PATH))) + ",");
   Out(KV("server_time", JInt((long)TimeTradeServer())) + ",");
   Out(KV("gmt_time", JInt((long)TimeGMT())) + ",");
   Out(KV("local_time", JInt((long)TimeLocal())) + ",");
   Out(KV("login", JInt(AccountInfoInteger(ACCOUNT_LOGIN))) + ",");
   Out(KV("server", JStr(AccountInfoString(ACCOUNT_SERVER))));
   Out("}");
   return(true);
  }

bool CmdAccount(string &err)
  {
   Out("\"data\":{");
   Out(KV("login", JInt(AccountInfoInteger(ACCOUNT_LOGIN))) + ",");
   Out(KV("name", JStr(AccountInfoString(ACCOUNT_NAME))) + ",");
   Out(KV("server", JStr(AccountInfoString(ACCOUNT_SERVER))) + ",");
   Out(KV("company", JStr(AccountInfoString(ACCOUNT_COMPANY))) + ",");
   Out(KV("currency", JStr(AccountInfoString(ACCOUNT_CURRENCY))) + ",");
   Out(KV("leverage", JInt(AccountInfoInteger(ACCOUNT_LEVERAGE))) + ",");
   Out(KV("trade_mode", JStr(EnumToString((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE)))) + ",");
   Out(KV("margin_mode", JStr(EnumToString((ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE)))) + ",");
   Out(KV("balance", JNum(AccountInfoDouble(ACCOUNT_BALANCE), 2)) + ",");
   Out(KV("equity", JNum(AccountInfoDouble(ACCOUNT_EQUITY), 2)) + ",");
   Out(KV("profit", JNum(AccountInfoDouble(ACCOUNT_PROFIT), 2)) + ",");
   Out(KV("margin", JNum(AccountInfoDouble(ACCOUNT_MARGIN), 2)) + ",");
   Out(KV("margin_free", JNum(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)) + ",");
   Out(KV("margin_level", JNum(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2)) + ",");
   Out(KV("server_time", JInt((long)TimeTradeServer())));
   Out("}");
   return(true);
  }

void WriteDeal(const ulong t)
  {
   Out("{");
   Out(KV("ticket", JInt((long)t)) + ",");
   Out(KV("order", JInt(HistoryDealGetInteger(t, DEAL_ORDER))) + ",");
   Out(KV("position", JInt(HistoryDealGetInteger(t, DEAL_POSITION_ID))) + ",");
   Out(KV("time_msc", JInt(HistoryDealGetInteger(t, DEAL_TIME_MSC))) + ",");
   Out(KV("type", JStr(EnumToString((ENUM_DEAL_TYPE)HistoryDealGetInteger(t, DEAL_TYPE)))) + ",");
   Out(KV("entry", JStr(EnumToString((ENUM_DEAL_ENTRY)HistoryDealGetInteger(t, DEAL_ENTRY)))) + ",");
   Out(KV("reason", JStr(EnumToString((ENUM_DEAL_REASON)HistoryDealGetInteger(t, DEAL_REASON)))) + ",");
   Out(KV("magic", JInt(HistoryDealGetInteger(t, DEAL_MAGIC))) + ",");
   Out(KV("symbol", JStr(HistoryDealGetString(t, DEAL_SYMBOL))) + ",");
   Out(KV("volume", JNum(HistoryDealGetDouble(t, DEAL_VOLUME), 8)) + ",");
   Out(KV("price", JNum(HistoryDealGetDouble(t, DEAL_PRICE), 8)) + ",");
   Out(KV("sl", JNum(HistoryDealGetDouble(t, DEAL_SL), 8)) + ",");
   Out(KV("tp", JNum(HistoryDealGetDouble(t, DEAL_TP), 8)) + ",");
   Out(KV("profit", JNum(HistoryDealGetDouble(t, DEAL_PROFIT), 2)) + ",");
   Out(KV("commission", JNum(HistoryDealGetDouble(t, DEAL_COMMISSION), 2)) + ",");
   Out(KV("swap", JNum(HistoryDealGetDouble(t, DEAL_SWAP), 2)) + ",");
   Out(KV("fee", JNum(HistoryDealGetDouble(t, DEAL_FEE), 2)) + ",");
   Out(KV("comment", JStr(HistoryDealGetString(t, DEAL_COMMENT))));
   Out("}");
  }

bool CmdDeals(string &err)
  {
   datetime from = (datetime)ParamLong("from", 0);
   datetime to   = (datetime)ParamLong("to", 0);
   if(to <= 0)
      to = TimeTradeServer() + 86400;
   if(!HistorySelect(from, to))
     {
      err = "HistorySelect failed, error " + IntegerToString(GetLastError());
      return(false);
     }
   int n = HistoryDealsTotal();
   Out("\"data\":{\"deals\":[");
   bool first = true;
   for(int i = 0; i < n; i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0)
         continue;
      if(!first)
         Out(",");
      WriteDeal(t);
      first = false;
     }
   Out("]}");
   return(true);
  }

bool CmdPositionDeals(string &err)
  {
   string parts[];
   int n = StringSplit(Param("ids"), ',', parts);
   if(n <= 0)
     {
      err = "ids is required";
      return(false);
     }
   Out("\"data\":{\"deals\":[");
   bool first = true;
   for(int k = 0; k < n; k++)
     {
      long pid = StringToInteger(parts[k]);
      if(pid <= 0 || !HistorySelectByPosition(pid))
         continue;
      int total = HistoryDealsTotal();
      for(int i = 0; i < total; i++)
        {
         ulong t = HistoryDealGetTicket(i);
         if(t == 0)
            continue;
         if(!first)
            Out(",");
         WriteDeal(t);
         first = false;
        }
     }
   Out("]}");
   return(true);
  }

bool ParseTf(const string name, ENUM_TIMEFRAMES &tf)
  {
   for(int i = 0; i < ArraySize(TF_NAMES); i++)
      if(TF_NAMES[i] == name)
        {
         tf = TF_VALS[i];
         return(true);
        }
   return(false);
  }

// Finds the broker's name for a symbol: exact, then case-insensitive, then with a suffix
// (EURUSD -> EURUSD.s / EURUSD+). A suffix must not start with a letter: EURUSDX is another
// instrument. Several suffixed variants: the one in Market Watch wins, otherwise it is an error.
bool ResolveSymbol(string &symbol, string &err)
  {
   bool custom = false;
   if(symbol == "")
     {
      err = "symbol is required";
      return(false);
     }
   if(SymbolExist(symbol, custom))
      return(true);
   string want = symbol;
   StringToUpper(want);
   int    want_len   = StringLen(want);
   string exact      = "";
   string any_match  = "";
   string sel_match  = "";
   string candidates = "";
   int    n_any      = 0;
   int    n_sel      = 0;
   int    total      = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string name = SymbolName(i, false);
      string up   = name;
      StringToUpper(up);
      if(up == want)
        {
         exact = name;
         break;
        }
      if(StringFind(up, want) != 0 || StringLen(up) <= want_len)
         continue;
      ushort next = StringGetCharacter(up, want_len);
      if(next >= 'A' && next <= 'Z')
         continue;
      n_any++;
      any_match = name;
      if(n_any <= 10)
         candidates += (candidates == "" ? "" : ", ") + name;
      if(SymbolInfoInteger(name, SYMBOL_SELECT))
        {
         n_sel++;
         sel_match = name;
        }
     }
   if(exact != "")
      symbol = exact;
   else
      if(n_sel == 1)
         symbol = sel_match;
      else
         if(n_any == 1)
            symbol = any_match;
         else
           {
            err = n_any > 1 ? "ambiguous symbol " + symbol + ", pick one of: " + candidates
                            : "unknown symbol: " + symbol;
            return(false);
           }
   return(true);
  }

bool EnsureSymbol(string &symbol, string &err)
  {
   if(!ResolveSymbol(symbol, err))
      return(false);
   if(!SymbolInfoInteger(symbol, SYMBOL_SELECT))
      SymbolSelect(symbol, true);
   return(true);
  }

bool CmdRates(string &err)
  {
   string symbol = Param("symbol");
   string tfname = Param("tf", "H1");
   ENUM_TIMEFRAMES tf = PERIOD_H1;
   if(!ParseTf(tfname, tf))
     {
      err = "unknown timeframe: " + tfname;
      return(false);
     }
   if(!EnsureSymbol(symbol, err))
      return(false);

   long from  = ParamLong("from", 0);
   long to    = ParamLong("to", 0);
   long count = ParamLong("count", 0);
   if(count <= 0)
      count = 100;
   if(count > InpMaxBars)
      count = InpMaxBars;

   MqlRates rates[];
   int got = -1;
   for(int attempt = 0; attempt < 4 && got <= 0; attempt++)
     {
      ResetLastError();
      if(from > 0)
         got = CopyRates(symbol, tf, (datetime)from, (datetime)(to > 0 ? to : (long)TimeTradeServer()), rates);
      else
         if(to > 0)
            got = CopyRates(symbol, tf, (datetime)to, (int)count, rates);
         else
            got = CopyRates(symbol, tf, 0, (int)count, rates);
      if(got <= 0)
         Sleep(150);
     }
   if(got < 0)
     {
      err = "history_loading: CopyRates failed, error " + IntegerToString(GetLastError());
      return(false);
     }

   int  start     = 0;
   bool truncated = false;
   if(got > InpMaxBars)
     {
      start     = got - InpMaxBars;
      truncated = true;
     }
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   Out("\"data\":{");
   Out(KV("symbol", JStr(symbol)) + "," + KV("tf", JStr(tfname)) + "," + KV("digits", JInt(digits)) + ",");
   Out(KV("truncated", JBool(truncated)) + ",\"rows\":[");
   for(int i = start; i < got; i++)
     {
      if(i > start)
         Out(",");
      Out("[" + JInt((long)rates[i].time) + "," + JNum(rates[i].open, digits) + "," + JNum(rates[i].high, digits) + "," +
          JNum(rates[i].low, digits) + "," + JNum(rates[i].close, digits) + "," + JInt(rates[i].tick_volume) + "," +
          JInt(rates[i].spread) + "]");
     }
   Out("]}");
   return(true);
  }

bool CmdPositions(string &err)
  {
   int n = PositionsTotal();
   Out("\"data\":{\"positions\":[");
   bool first = true;
   for(int i = 0; i < n; i++)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;
      if(!first)
         Out(",");
      first = false;
      string sym = PositionGetString(POSITION_SYMBOL);
      int    d   = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      Out("{");
      Out(KV("ticket", JInt((long)t)) + ",");
      Out(KV("position", JInt(PositionGetInteger(POSITION_IDENTIFIER))) + ",");
      Out(KV("symbol", JStr(sym)) + ",");
      Out(KV("type", JStr(EnumToString((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE)))) + ",");
      Out(KV("volume", JNum(PositionGetDouble(POSITION_VOLUME), 8)) + ",");
      Out(KV("price_open", JNum(PositionGetDouble(POSITION_PRICE_OPEN), d)) + ",");
      Out(KV("price_current", JNum(PositionGetDouble(POSITION_PRICE_CURRENT), d)) + ",");
      Out(KV("sl", JNum(PositionGetDouble(POSITION_SL), d)) + ",");
      Out(KV("tp", JNum(PositionGetDouble(POSITION_TP), d)) + ",");
      Out(KV("profit", JNum(PositionGetDouble(POSITION_PROFIT), 2)) + ",");
      Out(KV("swap", JNum(PositionGetDouble(POSITION_SWAP), 2)) + ",");
      Out(KV("time_msc", JInt(PositionGetInteger(POSITION_TIME_MSC))) + ",");
      Out(KV("magic", JInt(PositionGetInteger(POSITION_MAGIC))) + ",");
      Out(KV("comment", JStr(PositionGetString(POSITION_COMMENT))));
      Out("}");
     }
   Out("]}");
   return(true);
  }

bool CmdSymbol(string &err)
  {
   string symbol = Param("symbol");
   if(!EnsureSymbol(symbol, err))
      return(false);
   int     d = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   MqlTick tick;
   bool    has_tick = SymbolInfoTick(symbol, tick);
   Out("\"data\":{");
   Out(KV("symbol", JStr(symbol)) + ",");
   Out(KV("description", JStr(SymbolInfoString(symbol, SYMBOL_DESCRIPTION))) + ",");
   Out(KV("digits", JInt(d)) + ",");
   Out(KV("point", JNum(SymbolInfoDouble(symbol, SYMBOL_POINT), 10)) + ",");
   Out(KV("tick_size", JNum(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE), 10)) + ",");
   Out(KV("tick_value", JNum(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE), 8)) + ",");
   Out(KV("contract_size", JNum(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE), 8)) + ",");
   Out(KV("currency_base", JStr(SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE))) + ",");
   Out(KV("currency_profit", JStr(SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT))) + ",");
   Out(KV("volume_min", JNum(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), 8)) + ",");
   Out(KV("volume_step", JNum(SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP), 8)) + ",");
   Out(KV("spread", JInt(SymbolInfoInteger(symbol, SYMBOL_SPREAD))) + ",");
   if(has_tick)
     {
      Out(KV("bid", JNum(tick.bid, d)) + ",");
      Out(KV("ask", JNum(tick.ask, d)) + ",");
      Out(KV("tick_time_msc", JInt(tick.time_msc)));
     }
   else
      Out(KV("bid", "null") + "," + KV("ask", "null") + "," + KV("tick_time_msc", "null"));
   Out("}");
   return(true);
  }

//+------------------------------------------------------------------+
//| Heartbeat                                                        |
//+------------------------------------------------------------------+
void WriteHeartbeat()
  {
   string tmp = ROOT_DIR + "\\status.tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_BIN);
   if(h == INVALID_HANDLE)
      return;
   string s = "{" + KV("proto", JInt(PROTO_VERSION)) + "," +
              KV("ea_version", JStr(EA_VERSION)) + "," +
              KV("chart_symbol", JStr(_Symbol)) + "," +
              KV("login", JInt(AccountInfoInteger(ACCOUNT_LOGIN))) + "," +
              KV("server", JStr(AccountInfoString(ACCOUNT_SERVER))) + "," +
              KV("connected", JBool(TerminalInfoInteger(TERMINAL_CONNECTED) != 0)) + "," +
              KV("server_time", JInt((long)TimeTradeServer())) + "," +
              KV("data_path", JStr(TerminalInfoString(TERMINAL_DATA_PATH))) + "}";
   WriteUtf8(h, s);
   FileClose(h);
   FileMove(tmp, 0, ROOT_DIR + "\\status.json", FILE_REWRITE);
  }

//+------------------------------------------------------------------+
//| Request parsing (key=value lines, UTF-8)                         |
//+------------------------------------------------------------------+
string ReadAll(const string path)
  {
   int h = FileOpen(path, FILE_READ | FILE_BIN);
   if(h == INVALID_HANDLE)
      return("");
   string s    = "";
   int    size = (int)FileSize(h);
   if(size > 0)
     {
      uchar data[];
      ArrayResize(data, size);
      FileReadArray(h, data, 0, size);
      s = CharArrayToString(data, 0, size, CP_UTF8);
     }
   FileClose(h);
   return(s);
  }

void ParseParams(const string text)
  {
   ArrayResize(g_keys, 0);
   ArrayResize(g_vals, 0);
   string lines[];
   int n = StringSplit(text, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      string line = lines[i];
      StringReplace(line, "\r", "");
      int p = StringFind(line, "=");
      if(p <= 0)
         continue;
      int k = ArraySize(g_keys);
      ArrayResize(g_keys, k + 1);
      ArrayResize(g_vals, k + 1);
      g_keys[k] = StringSubstr(line, 0, p);
      g_vals[k] = StringSubstr(line, p + 1);
     }
  }

string Param(const string key, const string def = "")
  {
   for(int i = 0; i < ArraySize(g_keys); i++)
      if(g_keys[i] == key)
         return(g_vals[i]);
   return(def);
  }

long ParamLong(const string key, const long def = 0)
  {
   string v = Param(key, "");
   if(v == "")
      return(def);
   return(StringToInteger(v));
  }

//+------------------------------------------------------------------+
//| Response output (UTF-8, buffered, published atomically)          |
//+------------------------------------------------------------------+
bool BeginResponse(const string id)
  {
   g_buf = "";
   g_out = FileOpen(RESP_DIR + "\\" + id + ".tmp", FILE_WRITE | FILE_BIN);
   if(g_out == INVALID_HANDLE)
     {
      PrintFormat("MT5Bridge: cannot open response file, error %d", GetLastError());
      return(false);
     }
   return(true);
  }

void EndResponse(const string id)
  {
   Flush();
   FileClose(g_out);
   g_out = INVALID_HANDLE;
   if(!FileMove(RESP_DIR + "\\" + id + ".tmp", 0, RESP_DIR + "\\" + id + ".json", FILE_REWRITE))
      PrintFormat("MT5Bridge: cannot publish response %s, error %d", id, GetLastError());
  }

void Out(const string s)
  {
   StringAdd(g_buf, s);
   if(StringLen(g_buf) >= 65536)
      Flush();
  }

void Flush()
  {
   if(g_out == INVALID_HANDLE || StringLen(g_buf) == 0)
      return;
   WriteUtf8(g_out, g_buf);
   g_buf = "";
  }

void WriteUtf8(const int h, const string s)
  {
   uchar bytes[];
   int n = StringToCharArray(s, bytes, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && bytes[n - 1] == 0)
      n--;
   if(n > 0)
      FileWriteArray(h, bytes, 0, n);
  }

//+------------------------------------------------------------------+
//| JSON helpers                                                     |
//+------------------------------------------------------------------+
string JStr(const string s)
  {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\n", "\\n");
   StringReplace(r, "\r", "\\r");
   StringReplace(r, "\t", "\\t");
   return("\"" + r + "\"");
  }

string JNum(const double v, const int digits)
  {
   if(!MathIsValidNumber(v))
      return("null");
   return(DoubleToString(v, digits));
  }

string JInt(const long v)
  {
   return(IntegerToString(v));
  }

string JBool(const bool b)
  {
   return(b ? "true" : "false");
  }

string KV(const string key, const string json_value)
  {
   return(JStr(key) + ":" + json_value);
  }
//+------------------------------------------------------------------+
