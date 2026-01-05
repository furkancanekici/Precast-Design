import pandas as pd
import numpy as np
import joblib
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import math

app = FastAPI(title="Prefabrik Tasarım API v35")
data_store = {}

# --- MODELLER ---
class FiyatlarModel(BaseModel):
    fiyat_beton_c25: float; fiyat_beton_c30: float; fiyat_beton_c35: float
    fiyat_beton_c40: float; fiyat_beton_c50: float
    fiyat_celik: float; fiyat_iscilik: float

class GirdiModelKolon(BaseModel):
    il: str; zemin_sinifi: str; cati_kilo: float
    istenen_uzunluk_metre: float; fiyatlar: FiyatlarModel

class CiktiKolon(BaseModel):
    b_mm: float; h_mm: float; Length_mm: float; onrete_lass: float
    Donati_Orani_yuzde: float; Hesaplanan_Periyot_T_s: float
    Hesaplanan_M_Demand_kNm: float; Hesaplanan_Sd_mm: float
    Maliyet_Endeksi: float; Moment_Yeterli_Skoru_yuzde: float
    Hesaplanan_Mr_Kapasite_kNm: float; Fiziksel_Kapasite_Orani_yuzde: float 
    Ag_mm2: float; As_mm2: float; Kullanilan_Beton_Fiyati: float

class CiktiModelKolon(BaseModel):
    mesaj: str; ss_degeri: Optional[float] = None; s1_degeri: Optional[float] = None
    sds_degeri: Optional[float] = None; sd1_degeri: Optional[float] = None
    secilen_konum_lat: Optional[float] = None; secilen_konum_lon: Optional[float] = None
    uygun_kolonlar: List[CiktiKolon] = []

class GirdiModelSistem(BaseModel):
    il: str; zemin_sinifi: str
    total_cati_yuku_kg: float
    kolon_boyu_m: float; kiris_acikligi_m: float
    aks_adedi: int; aks_araligi_m: float
    fiyatlar: FiyatlarModel

class CiktiSistem(BaseModel):
    kolon: CiktiKolon
    kiris_b_mm: float; kiris_h_mm: float; kiris_beton: str
    kiris_sehim_mm: float; kiris_maliyet: float; kiris_sehim_limit_mm: float
    sistem_toplam_maliyet: float

class CiktiModelSistem(BaseModel):
    mesaj: str; ss_degeri: Optional[float] = None; s1_degeri: Optional[float] = None
    sds_degeri: Optional[float] = None; sd1_degeri: Optional[float] = None
    secilen_konum_lat: Optional[float] = None; secilen_konum_lon: Optional[float] = None
    uygun_sistemler: List[CiktiSistem] = []

# --- BAŞLATMA ---
@app.on_event("startup")
def load_data():
    print("API v35 Başlatılıyor...")
    path = "." 
    try:
        data_store['df_afad'] = pd.read_excel(os.path.join(path, "AAD_İller_İlceler_AFAD_Values.xlsx"))
        data_store['df_afad_unique'] = data_store['df_afad'][['il', 'Ss', 'S1', 'Soil Class']].drop_duplicates(subset=['il', 'Soil Class']).reset_index(drop=True)
        
        df_k = pd.read_excel(os.path.join(path, "kolkat.xlsx"))
        cols = ['b (mm)', 'h (mm)', 'onrete lass', 'Donatı Oranı (%)', 'Ag (mm²)', 'As (mm²)', 'I (mm⁴)', 'Ec (MPa)']
        data_store['unique_kesitler'] = df_k[cols].drop_duplicates().reset_index(drop=True)
        
        data_store['model'] = joblib.load(os.path.join(path, "kolon_oneri_modeli_rf_sample.joblib"))
        
        try:
            df_c = pd.read_excel(os.path.join(path, "il_ilce_koordinatlari_duzenlenmis.xlsx"))
            if {'lat', 'lon'}.issubset(df_c.columns): df_c = df_c.rename(columns={'lat': 'latitude', 'lon': 'longitude'})
            data_store['df_coords'] = df_c
        except: data_store['df_coords'] = None
        
        k = []
        for b in range(250, 850, 50):
            for h in range(400, 1500, 50):
                if h >= b: k.append({'b': b, 'h': h, 'beton': 'C30'})
        data_store['kiris_katalogu'] = pd.DataFrame(k)
        
        data_store['varsayilan_fiyatlar'] = {'C25': 3151.28, 'C30': 3276.28, 'C35': 3401.28, 'C40': 3526.28, 'C50': 3776.28, 'celik': 21000.0, 'kalip_iscilik': 1500.0}
        print("✅ Sistem Hazır.")
    except Exception as e: print(f"❌ Hata: {e}"); 

@app.get("/varsayilan-fiyatlar")
async def get_fiyatlar(): return data_store.get('varsayilan_fiyatlar', {})

# --- HESAPLAMA ---
def _hesapla_kolon_internal(il, zemin, yuk, boy, fiyatlar):
    try:
        df = data_store['unique_kesitler'].copy(); model = data_store['model']
        row = data_store['df_afad_unique']; row = row[(row['il'] == il) & (row['Soil Class'] == zemin)]
        if row.empty: return None, None, None, None, pd.DataFrame()
        Ss, S1 = float(row.iloc[0]['Ss']), float(row.iloc[0]['S1'])
        frow = data_store['df_afad']; frow = frow[(frow['il'] == il) & (frow['Soil Class'] == zemin)]
        if frow.empty: return None, None, None, None, pd.DataFrame()
        SDs, SD1 = float(frow.iloc[0]['SDs']), float(frow.iloc[0]['SD1'])

        L_m = boy; df['Length (mm)'] = L_m * 1000
        df['m_kg'] = (df['Ag (mm²)']/1e6 * L_m * 2500) + yuk
        df['k_Nm'] = (3 * df['Ec (MPa)']*1e6 * (df['I (mm⁴)']/1e12)) / (L_m**3)
        k_safe = np.where(df['k_Nm'] <= 0, 1e-6, df['k_Nm'])
        df['Hesaplanan_Periyot_T (s)'] = 2 * np.pi * np.sqrt(df['m_kg'] / k_safe)
        
        T = df['Hesaplanan_Periyot_T (s)'].values; TA = 0.2 * SD1 / SDs; TB = SD1 / SDs; TL = 6.0
        cond1 = T < TA; cond2 = (T >= TA) & (T <= TB); cond3 = (T > TB) & (T <= TL); cond4 = T > TL
        Sa = np.zeros_like(T)
        Sa[cond1] = (0.4 + 0.6 * (T[cond1] / TA)) * SDs; Sa[cond2] = SDs; Sa[cond3] = SD1 / T[cond3]
        T_safe = np.where(T <= 0, 0.001, T); Sa[cond4] = SD1 * TL / (T_safe[cond4]**2)
        df['Hesaplanan_Sa (g)'] = Sa
        
        F = df['m_kg'] * df['Hesaplanan_Sa (g)'] * 9.81
        df['Hesaplanan_M_Demand (kNm)'] = (F * L_m) / 1000.0
        df['Hesaplanan_Sd (mm)'] = df['Hesaplanan_Sa (g)'] * 9.81 * (df['Hesaplanan_Periyot_T (s)'] / (2*np.pi))**2 * 1000
        df['Deplasman_Orani'] = df['Hesaplanan_Sd (mm)'] / (L_m * 1000)

        feats = ['b (mm)', 'h (mm)', 'Length (mm)', 'onrete lass', 'Donatı Oranı (%)']
        X = df[feats].copy(); X['Input_Ss']=Ss; X['Input_S1']=S1; X['Input_Cati_Kutlesi_ton']=yuk/1000; X['Input_Zemin']=zemin
        X = pd.get_dummies(X, columns=['Input_Zemin'], drop_first=True)
        for c in ['Input_Zemin_ZB', 'Input_Zemin_ZC', 'Input_Zemin_ZD', 'Input_Zemin_ZE']: 
            if c not in X.columns: X[c] = 0
        req = ['b (mm)', 'h (mm)', 'Length (mm)', 'onrete lass', 'Donatı Oranı (%)', 'Input_Ss', 'Input_S1', 'Input_Cati_Kutlesi_ton', 'Input_Zemin_ZB', 'Input_Zemin_ZC', 'Input_Zemin_ZD', 'Input_Zemin_ZE']
        df['Moment_Yeterli_Skoru (%)'] = model.predict_proba(X[req])[:, 1] * 100.0

        fcd = df['onrete lass']/1.5; fyd=420/1.15; d=df['h (mm)']-50
        a = np.minimum((df['As (mm²)']*fyd)/(0.85*fcd*df['b (mm)']), d)
        df['Hesaplanan_Mr_Kapasite (kNm)'] = (df['As (mm²)']*fyd*(d-a/2))/1e6
        df['Fiziksel_Kapasite_Orani (%)'] = np.where(df['Hesaplanan_Mr_Kapasite (kNm)']>0, (df['Hesaplanan_M_Demand (kNm)']/df['Hesaplanan_Mr_Kapasite (kNm)'])*100, 999)

        mask = (df['Deplasman_Orani']<=0.02) & (df['Moment_Yeterli_Skoru (%)']>=50) & (df['Fiziksel_Kapasite_Orani (%)']<=100)
        df_ok = df[mask].copy()
        if df_ok.empty: return Ss, S1, SDs, SD1, df_ok

        def get_p(r):
            c = int(r['onrete lass'])
            if c==25: return fiyatlar.fiyat_beton_c25
            elif c==30: return fiyatlar.fiyat_beton_c30
            elif c==35: return fiyatlar.fiyat_beton_c35
            elif c==40: return fiyatlar.fiyat_beton_c40
            elif c==50: return fiyatlar.fiyat_beton_c50
            return fiyatlar.fiyat_beton_c30
        
        df_ok['Birim_Beton_Fiyati'] = df_ok.apply(get_p, axis=1)
        vol = (df_ok['Ag (mm²)']/1e6)*L_m; stl = (df_ok['As (mm²)']/1e6)*L_m*7.85
        df_ok['Maliyet_Endeksi'] = (vol*df_ok['Birim_Beton_Fiyati']) + (stl*fiyatlar.fiyat_celik) + (vol*fiyatlar.fiyat_iscilik)
        df_ok = df_ok.sort_values(by=['Moment_Yeterli_Skoru (%)', 'Maliyet_Endeksi'], ascending=[False, True])
        return Ss, S1, SDs, SD1, df_ok
    except Exception as e:
        print(f"İç Hesap Hatası: {e}"); return None, None, None, None, pd.DataFrame()

@app.post("/hesapla", response_model=CiktiModelKolon)
async def hesapla_tekil(girdi: GirdiModelKolon):
    try:
        Ss, S1, SDs, SD1, df = _hesapla_kolon_internal(girdi.il, girdi.zemin_sinifi, girdi.cati_kilo, girdi.istenen_uzunluk_metre, girdi.fiyatlar)
        lat, lon = None, None
        if data_store['df_coords'] is not None:
            c = data_store['df_coords']; r = c[c['il']==girdi.il]; 
            if r.empty: r = c[c['ilce']==girdi.il]
            if not r.empty: lat, lon = float(r.iloc[0]['latitude']), float(r.iloc[0]['longitude'])
        if df.empty: 
            if Ss is None: return CiktiModelKolon(mesaj="Veri hatası.")
            return CiktiModelKolon(mesaj="Kriterlere uygun kolon bulunamadı.", ss_degeri=Ss, s1_degeri=S1, secilen_konum_lat=lat, secilen_konum_lon=lon)
        res = []
        for _, r in df.head(10).iterrows():
            res.append(CiktiKolon(b_mm=r['b (mm)'], h_mm=r['h (mm)'], Length_mm=r['Length (mm)'], onrete_lass=r['onrete lass'], Donati_Orani_yuzde=r['Donatı Oranı (%)'], Hesaplanan_Periyot_T_s=r['Hesaplanan_Periyot_T (s)'], Hesaplanan_M_Demand_kNm=r['Hesaplanan_M_Demand (kNm)'], Hesaplanan_Sd_mm=r['Hesaplanan_Sd (mm)'], Maliyet_Endeksi=r['Maliyet_Endeksi'], Moment_Yeterli_Skoru_yuzde=r['Moment_Yeterli_Skoru (%)'], Hesaplanan_Mr_Kapasite_kNm=r['Hesaplanan_Mr_Kapasite (kNm)'], Fiziksel_Kapasite_Orani_yuzde=r['Fiziksel_Kapasite_Orani (%)'], Ag_mm2=r['Ag (mm²)'], As_mm2=r['As (mm²)'], Kullanilan_Beton_Fiyati=r['Birim_Beton_Fiyati']))
        return CiktiModelKolon(mesaj="Başarılı", ss_degeri=Ss, s1_degeri=S1, sds_degeri=SDs, sd1_degeri=SD1, secilen_konum_lat=lat, secilen_konum_lon=lon, uygun_kolonlar=res)
    except Exception as e: print(f"Endpoint Hatası: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.post("/hesapla-sistem", response_model=CiktiModelSistem)
async def hesapla_sistem(girdi: GirdiModelSistem):
    try:
        df_k = data_store['kiris_katalogu'].copy()
        L = girdi.kiris_acikligi_m; limit = (L*1000)/200
        Ec = 30000 * 1000; rho = 25
        uygun_kirisler = []
        for _, k in df_k.iterrows():
            b, h = k['b']/1000, k['h']/1000
            I = (b*h**3)/12; q = b*h*rho
            delta = ((5*q*(L**4))/(384*Ec*I))*1000
            if delta <= limit:
                vol = b*h*L; stl = vol*0.02*7.85
                cost = (vol*girdi.fiyatlar.fiyat_beton_c30) + (stl*girdi.fiyatlar.fiyat_celik) + (vol*girdi.fiyatlar.fiyat_iscilik)
                k_dict = k.to_dict(); k_dict['maliyet']=cost; k_dict['sehim']=delta; k_dict['q']=q
                uygun_kirisler.append(k_dict)
        
        if not uygun_kirisler: return CiktiModelSistem(mesaj="Bu açıklık için uygun kiriş bulunamadı.")
        uygun_kirisler.sort(key=lambda x: x['maliyet'])
        top_kirisler = uygun_kirisler[:5]
        sistemler = []
        n_kolon = girdi.aks_adedi * 2; n_kolon = 2 if n_kolon==0 else n_kolon
        lat, lon = None, None
        if data_store['df_coords'] is not None:
            c = data_store['df_coords']; r = c[c['il']==girdi.il]; 
            if r.empty: r = c[c['ilce']==girdi.il]
            if not r.empty: lat, lon = float(r.iloc[0]['latitude']), float(r.iloc[0]['longitude'])
        Ss_out, S1_out, SDs_out, SD1_out = None, None, None, None
        for kiris in top_kirisler:
            tekil_yuk = (girdi.total_cati_yuku_kg / n_kolon) + ((kiris['q']*L*1000/9.81)/2)
            Ss, S1, SDs, SD1, df_col = _hesapla_kolon_internal(girdi.il, girdi.zemin_sinifi, tekil_yuk, girdi.kolon_boyu_m, girdi.fiyatlar)
            if Ss_out is None: Ss_out, S1_out, SDs_out, SD1_out = Ss, S1, SDs, SD1
            if df_col.empty: continue 
            secilen_col = None
            for _, row in df_col.iterrows():
                if row['b (mm)'] >= kiris['b']: secilen_col = row; break
            if secilen_col is None: secilen_col = df_col.iloc[0]
            sys_cost = (kiris['maliyet'] * girdi.aks_adedi) + (secilen_col['Maliyet_Endeksi'] * n_kolon)
            col_obj = CiktiKolon(b_mm=secilen_col['b (mm)'], h_mm=secilen_col['h (mm)'], Length_mm=secilen_col['Length (mm)'], onrete_lass=secilen_col['onrete lass'], Donati_Orani_yuzde=secilen_col['Donatı Oranı (%)'], Hesaplanan_Periyot_T_s=secilen_col['Hesaplanan_Periyot_T (s)'], Hesaplanan_M_Demand_kNm=secilen_col['Hesaplanan_M_Demand (kNm)'], Hesaplanan_Sd_mm=secilen_col['Hesaplanan_Sd (mm)'], Maliyet_Endeksi=secilen_col['Maliyet_Endeksi'], Moment_Yeterli_Skoru_yuzde=secilen_col['Moment_Yeterli_Skoru (%)'], Hesaplanan_Mr_Kapasite_kNm=secilen_col['Hesaplanan_Mr_Kapasite (kNm)'], Fiziksel_Kapasite_Orani_yuzde=secilen_col['Fiziksel_Kapasite_Orani (%)'], Ag_mm2=secilen_col['Ag (mm²)'], As_mm2=secilen_col['As (mm²)'], Kullanilan_Beton_Fiyati=secilen_col['Birim_Beton_Fiyati'])
            sys_obj = CiktiSistem(kolon=col_obj, kiris_b_mm=kiris['b'], kiris_h_mm=kiris['h'], kiris_beton=kiris['beton'], kiris_sehim_mm=kiris['sehim'], kiris_maliyet=kiris['maliyet'], kiris_sehim_limit_mm=limit, sistem_toplam_maliyet=sys_cost)
            sistemler.append(sys_obj)
        if not sistemler: return CiktiModelSistem(mesaj="Kiriş var ama uygun kolon bulunamadı.", ss_degeri=Ss_out, s1_degeri=S1_out)
        sistemler.sort(key=lambda x: x.sistem_toplam_maliyet)
        return CiktiModelSistem(mesaj="Başarılı", ss_degeri=Ss_out, s1_degeri=S1_out, sds_degeri=SDs_out, sd1_degeri=SD1_out, secilen_konum_lat=lat, secilen_konum_lon=lon, uygun_sistemler=sistemler)
    except Exception as e: print(f"Sistem Hatası: {e}"); raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)