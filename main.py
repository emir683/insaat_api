import os
import gc
import json # Loglama için gerekli
from flask import Flask, request, jsonify
import ezdxf
from ezdxf.lldxf import tagger
import re
import math
import cloudconvert

app = Flask(__name__)

# ==========================================
# 🔑 AYARLAR
# ==========================================
CLOUDCONVERT_API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiYWQzMmEyODgwOGUwZjFkYjI0YTU1Zjk1YTM1NjE5NjA2YTg3MjRjNDY0Yjc4ZTQ1Y2NhNGFlY2E0NTg1NGM0MTc1MmQ3YmM1MjZkMjQ5NjEiLCJpYXQiOjE3NjU1NDYzNTIuNzIxNDQ1LCJuYmYiOjE3NjU1NDYzNTIuNzIxNDQ3LCJleHAiOjQ5MjEyMTk5NTIuNzEzNDM2LCJzdWIiOiI3MzcxNzA2MyIsInNjb3BlcyI6WyJ0YXNrLndyaXRlIiwidGFzay5yZWFkIiwidXNlci5yZWFkIl19.Lr7QFvOfWu2qss8lt3JKRtQrUGP1LWXAPQG5gm7GmNtqdMQ9Nu3TUAsNIn1LhLqd46vq8tqpXdW-OOB4_dh_sAG1vWsZeGWBYxFxaeQJdDuZdpJ5Bc2NntvYrmfTHK8XTjan83NpibvgCz9Aviho2rv7lLciumaeuEr2rqmnP12jvdIKzLoDOCLPyd1WWu0_LWmQdVZyEGMtoon6MxWnxbMgmVh-Lfn0I7AyvWlgYm85IeL4ioLQBjebMhFqmYNfp5ZJy6VtmmEgxMQhZftYwsaPtp9bBb7gfUBmz_Gj9IYSgr7wWbMVjjHul_JAqgEl7adcaeK3JdjigtaVc6MjvZEqVaUetdCwMxqqcrufkNFYaE0NfjfOSjcO_gX5xn3xulMerzR92nzsGfk8LldRBtnTaACjEfMP8-noHZvpMzCMWBtvrP2FboYO06FUaT9hr8rRKwFrkIAeA516WNYwwdAeFSLiLpTzCZdRSweir8UKl3TtiA7s9Gk6F7zgocgQiIOSt4Hz7HXFVj--v3XuD8dNyZSQsv-niqzK8-CzFw7CDewYH57Vp_JgFv36yn117rsp_G4dw9COfmdS4l6Au27BHVmER7aG-2C2FTnulyqIhbRgidPjKClo_mMmgxTkNFRR1JpLiBtpsGYhdiDmQ7RseVyQYABMUo415cca3Yw" 

cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ==========================================
# 🏗️ HESAPLAMA MOTORU (Düşük RAM Modu)
# ==========================================
class RebarExtractor:
    def __init__(self):
        self.rebar_pattern = re.compile(r'(\d+)\s*[Ø|Q|q|fi]\s*(\d+)(?:\s*L\s*=\s*(\d+))?', re.IGNORECASE)

    def parse_dxf_stream(self, file_path):
        extracted_data = []
        try:
            # Encoding hatası almamak için errors='ignore' ekledik
            with open(file_path, 'rt', encoding='cp1252', errors='ignore') as fp:
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
            return {"error": f"Stream okuma hatası: {str(e)}"}

class MaterialCalculator:
    def __init__(self):
        self.unit_weights = {8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.580, 18: 2.000, 20: 2.470}
        self.stock_bar_length_m = 12.0 

    def calculate_needs(self, parsed_data):
        summary = {} 
        for item in parsed_data:
            diameter = item['diameter']
            count = item['count']
            length_cm = item['length_cm']
            
            if diameter not in self.unit_weights: continue

            length_m = length_cm / 100.0
            total_item_length_m = length_m * count

            if diameter not in summary: summary[diameter] = {"total_length_m": 0.0}
            summary[diameter]["total_length_m"] += total_item_length_m

        final_report = {}
        total_project_tonnage = 0.0

        for dia, data in summary.items():
            total_len = data["total_length_m"]
            unit_w = self.unit_weights[dia]
            weight_kg = total_len * unit_w
            stock_bars = math.ceil(total_len / self.stock_bar_length_m)

            final_report[f"Q{dia}"] = {
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
# ☁️ CLOUDCONVERT (GÜÇLENDİRİLMİŞ VERSİYON)
# ==========================================
def convert_dwg_to_dxf(input_path):
    try:
        print("CloudConvert işlemi başlatılıyor...")
        
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

        # 2. Dosyayı Yükle
        upload_task = next(task for task in job['tasks'] if task['name'] == 'import-my-file')
        
        with open(input_path, 'rb') as f:
            cloudconvert.Task.upload(file_name=input_path, task=upload_task)

        # 3. İşlemin Bitmesini Bekle
        job = cloudconvert.Job.wait(id=job['id'])
        
        # 4. Hata Kontrolü (ÖNEMLİ KISIM)
        if job['status'] == 'error':
            print("CloudConvert Hatası (Job Failed):", json.dumps(job, indent=2))
            return None

        # 5. İndirme Linkini Bul
        export_task = next(task for task in job['tasks'] if task['name'] == 'export-my-file')
        
        if export_task['status'] != 'finished':
            print("Export görevi tamamlanamadı:", export_task)
            return None
            
        file_url = export_task['result']['files'][0]['url']
        
        # 6. Dosyayı İndir
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
    return "İnşaat API (Final Sürüm) Çalışıyor! 🏗️"

@app.route('/analiz-et', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    
    file = request.files['file']
    filename = file.filename.lower()
    
    # Render'da /tmp klasörü yazılabilir tek yerdir
    filepath = os.path.join("/tmp", file.filename)
    file.save(filepath)

    target_dxf_path = filepath
    converted_file_created = False

    try:
        # DWG Dönüştürme
        if filename.endswith('.dwg'):
            print(f"DWG tespit edildi: {filename}")
            converted_path = convert_dwg_to_dxf(filepath)
            
            if converted_path:
                target_dxf_path = converted_path
                converted_file_created = True
            else:
                return jsonify({'error': 'DWG dönüştürme hatası. Lütfen geçerli bir DWG yükleyin.'}), 500

        # Veriyi Çıkar
        print(f"Analiz ediliyor: {target_dxf_path}")
        extractor = RebarExtractor()
        raw_data = extractor.parse_dxf_stream(target_dxf_path)

        if isinstance(raw_data, dict) and "error" in raw_data:
            return jsonify(raw_data), 500
        
        # Eğer hiç veri bulunamadıysa uyar
        if not raw_data:
             return jsonify({
                 'error': 'Dosyada okunabilir demir verisi bulunamadı. Lütfen dosyanın metin (text) içerdiğinden emin olun.',
                 'demir_listesi': {},
                 'toplam_tonaj_kg': 0
             }), 200

        # Hesabı Yap
        calculator = MaterialCalculator()
        result = calculator.calculate_needs(raw_data)
        
        return jsonify(result)

    except Exception as e:
        print(f"Sunucu Hatası: {e}")
        return jsonify({'error': f'Sunucu Hatası: {str(e)}'}), 500

    finally:
        # TEMİZLİK
        try:
            if os.path.exists(filepath): os.remove(filepath)
            if converted_file_created and os.path.exists(target_dxf_path):
                os.remove(target_dxf_path)
            gc.collect()
        except:
            pass

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
