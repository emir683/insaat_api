import os
import gc
import json
import re
import math
import unicodedata
from flask import Flask, request, jsonify
from ezdxf.lldxf import tagger
import cloudconvert

app = Flask(__name__)

# ==========================================
# 🔑 AYARLAR
# ==========================================
CLOUDCONVERT_API_KEY = """
eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiMTc1NTNjODEwNDAxYmRlZWU2ODZlMWViZDIyODVhYWY4Y2YwYzZmZTczMTQ0YTFmYzk2NGI4MWQ4MTM1MDY3Yzk4YmFlNmE2N2U4ZDUwOGMiLCJpYXQiOjE3NjU1NTg0NDYuODQzNjIxLCJuYmYiOjE3NjU1NTg0NDYuODQzNjIyLCJleHAiOjQ5MjEyMzIwNDYuODM5NTY0LCJzdWIiOiI3MzcxNzA2MyIsInNjb3BlcyI6WyJ1c2VyLnJlYWQiLCJ1c2VyLndyaXRlIiwidGFzay5yZWFkIiwidGFzay53cml0ZSJdfQ.Ot3krRvghGyEInTmF5SpjD_hszWHuMhYjTnhmQXVoGsZTYyXzwfnJGlbQv0BzTZpG6mGrED_yufHrtLctZQVUUeQVkElyEIMkcFys-uKt8EHnFHof9rMFL5JpGLzOr_3bunUeB8AtSXjeJae0Boj81ZOgZBJ8zV79_13rHhIN0vdxJ5BkffDcorxFGzImjJnSPT5lEEmA0Wce9XtvE1JGvFnZ3EIo9ag86vjTadANq_qsrjMWMYuisaRz6xTeOfcutnYaFQdheFFBSDhb-kDbogZsL4GjjlGszechORtjdqQoqX1IC4sDKS0mFt9Tk48rVKBBPsJTsukpETtLxjqoTBN4xE6k0dghc3sH6XnpGOLuzZTakrCSqQqjY1D29IbyGqowLD9xs6wldX-Lk80yhdZJ486QmwcwZee3hD9zYSIEXg1BOhESMzEau_qEcuEB4g1exYBhgpqvU3nV0EvH1gdcM-keK3qi7RG0mWyDJSNrgozvPH-1CdZ4ruibhcXGCvo2JF50H6q-5MdZ0L0SPMeLyhb679BaGKxPY33ta4zFkLkeObPS_rSZtupKyI4JmDzGBbfq6yqKPP0hVfT03Mv5ir_W7io_loD3DRV4rSalzIou1dtJttJICbI4PXyQttKNQmdxAMjA2fruO1Vl0-W4P30LbIQRZv55Ev0Qks

""".strip()

cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ==========================================
# 🧹 DOSYA ADI TEMİZLEYİCİ
# ==========================================
def secure_filename(filename):
    """Dosya adındaki Türkçe karakterleri ve boşlukları temizler."""
    filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    filename = re.sub(r'[^\w\s.-]', '', filename).strip().lower()
    return re.sub(r'[-\s]+', '_', filename)

# ==========================================
# 🏗️ HESAPLAMA MOTORU (Manuel Okuma)
# ==========================================
class RebarExtractor:
    def __init__(self):
        # Esnek Regex
        self.rebar_pattern = re.compile(
            r'(\d+)\s*(?:adet|ad)?\s*[xX*-]?\s*[Ø|Q|q|fi|FI|Fi|N]\s*[-]?\s*(\d+)(?:\s*(?:L|l|Boy|boy)[=:]?\s*(\d+))?', 
            re.IGNORECASE
        )

    def parse_dxf_stream(self, file_path):
        extracted_data = []
        try:
            with open(file_path, 'r', encoding='cp1252', errors='ignore') as fp:
                tag_stream = tagger.low_level_tagger(fp)
                in_text_entity = False
                
                for tag in tag_stream:
                    if tag.code == 0:
                        in_text_entity = (tag.value == 'TEXT' or tag.value == 'MTEXT')
                    
                    if in_text_entity and tag.code == 1 and isinstance(tag.value, str):
                        match = self.rebar_pattern.search(tag.value)
                        if match:
                            try:
                                count = int(match.group(1))
                                diameter = int(match.group(2))
                                if diameter > 40: continue 
                                length = int(match.group(3)) if match.group(3) else 0
                                
                                extracted_data.append({
                                    "raw_text": tag.value,
                                    "count": count,
                                    "diameter": diameter,
                                    "length_cm": length
                                })
                            except:
                                continue
            return extracted_data
        except Exception as e:
            print(f"DXF Okuma Hatası: {e}")
            return {"error": f"Okuma hatası: {str(e)}"}

class MaterialCalculator:
    def __init__(self):
        self.unit_weights = {8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.580, 18: 2.000, 20: 2.470, 22: 2.980, 24: 3.550, 26: 4.170, 28: 4.830, 32: 6.310}
        self.stock_bar_length_m = 12.0 

    def calculate_needs(self, parsed_data):
        summary = {} 
        for item in parsed_data:
            diameter = item['diameter']
            count = item['count']
            length_cm = item['length_cm']
            
            if diameter not in self.unit_weights: continue

            calc_length_m = (length_cm / 100.0) if length_cm > 0 else 1.0
            total_item_length_m = calc_length_m * count

            if diameter not in summary: summary[diameter] = {"total_length_m": 0.0}
            summary[diameter]["total_length_m"] += total_item_length_m

        final_report = {}
        total_project_tonnage = 0.0

        for dia, data in summary.items():
            total_len = data["total_length_m"]
            unit_w = self.unit_weights[dia]
            weight_kg = total_len * unit_w
            stock_bars = math.ceil(total_len / self.stock_bar_length_m)

            final_report[f"Ø{dia}"] = {
                "toplam_agirlik_kg": round(weight_kg, 2),
                "toplam_metraj_m": round(total_len, 2),
                "gerekli_cubuk_adet": stock_bars
            }
            total_project_tonnage += weight_kg

        return {
            "demir_listesi": final_report,
            "toplam_tonaj_kg": round(total_project_tonnage, 2),
            "okunan_veri_sayisi": len(parsed_data)
        }

# ==========================================
# ☁️ CLOUDCONVERT (DÜZELTİLEN KISIM)
# ==========================================
def convert_dwg_to_dxf(input_path):
    try:
        print(f"CloudConvert işlemi başlatılıyor: {input_path}")
        
        # 1. Görevi Oluştur
        job = cloudconvert.Job.create(payload={
            "tag": "dwg_to_dxf",
            "tasks": {
                "import-my-file": {
                    "operation": "import/upload"
                },
                "convert-my-file": {
                    "operation": "convert",
                    "input": "import-my-file",
                    "output_format": "dxf"
                },
                "export-my-file": {
                    "operation": "export/url",
                    "input": "convert-my-file"
                }
            }
        })

        job_data = job['data'] if 'data' in job else job
        
        if 'tasks' not in job_data:
            print("HATA: 'tasks' bulunamadı.")
            return None

        # 2. Dosyayı Yükle (DÜZELTME BURADA)
        # Artık dosyayı biz açmıyoruz, sadece yolu (input_path) veriyoruz.
        # Kütüphane kendisi hallediyor.
        upload_task = next(task for task in job_data['tasks'] if task['name'] == 'import-my-file')
        cloudconvert.Task.upload(file_name=input_path, task=upload_task)

        # 3. Bekle
        job = cloudconvert.Job.wait(id=job_data['id'])
        job_data = job['data'] if 'data' in job else job

        if job_data['status'] == 'error':
            print("CloudConvert Hatası:", json.dumps(job_data, indent=2))
            return None

        # 4. İndir
        export_task = next(task for task in job_data['tasks'] if task['name'] == 'export-my-file')
        file_url = export_task['result']['files'][0]['url']
        
        output_filename = input_path + ".dxf"
        cloudconvert.download(filename=output_filename, url=file_url)
        
        print("Dönüştürme başarılı:", output_filename)
        return output_filename

    except Exception as e:
        print(f"Convert Hatası Detaylı: {str(e)}")
        return None

# ==========================================
# 🌐 WEB SUNUCUSU
# ==========================================
@app.route('/', methods=['GET'])
def home():
    return "İnşaat API (Fix Upload) Çalışıyor! 🏗️"

@app.route('/analiz-et', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    
    file = request.files['file']
    
    # Dosya adını temizle ve kaydet
    clean_name = secure_filename(file.filename)
    filepath = os.path.join("/tmp", clean_name)
    file.save(filepath)

    target_dxf_path = filepath
    converted_file_created = False

    try:
        # DWG ise Çevir
        if clean_name.endswith('.dwg'):
            print(f"DWG tespit edildi: {clean_name}")
            # DÜZELTME: Sadece dosya yolunu gönderiyoruz
            converted_path = convert_dwg_to_dxf(filepath)
            
            if converted_path:
                target_dxf_path = converted_path
                converted_file_created = True
            else:
                return jsonify({'error': 'DWG dönüştürülemedi.'}), 500

        print(f"Analiz ediliyor: {target_dxf_path}")
        extractor = RebarExtractor()
        raw_data = extractor.parse_dxf_stream(target_dxf_path)

        if isinstance(raw_data, dict) and "error" in raw_data:
            return jsonify(raw_data), 500
        
        if not raw_data:
             return jsonify({
                 'error': 'Dosya okundu ancak demir verisi bulunamadı.',
                 'demir_listesi': {},
                 'toplam_tonaj_kg': 0
             }), 200

        calculator = MaterialCalculator()
        result = calculator.calculate_needs(raw_data)
        return jsonify(result)

    except Exception as e:
        print(f"Sunucu Hatası: {e}")
        return jsonify({'error': f'Sunucu Hatası: {str(e)}'}), 500

    finally:
        try:
            if os.path.exists(filepath): os.remove(filepath)
            if converted_file_created and os.path.exists(target_dxf_path):
                os.remove(target_dxf_path)
            gc.collect()
        except:
            pass

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
