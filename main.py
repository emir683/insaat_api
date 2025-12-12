import os
import gc
import json
import logging
import re
import math
import tempfile
import requests
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

import cloudconvert

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==========================================
# 🔑 AYARLAR (Güvenli şekilde ortam değişkeninden alınır)
# ==========================================
CLOUDCONVERT_API_KEY = os.getenv("CLOUDCONVERT_API_KEY")
if not CLOUDCONVERT_API_KEY:
    logger.error("CLOUDCONVERT_API_KEY ortam değişkeni tanımlı değil. Lütfen ayarlayın.")
    raise RuntimeError("CLOUDCONVERT_API_KEY yok. Ortam değişkeni olarak ayarlayın.")

cloudconvert.configure(api_key=CLOUDCONVERT_API_KEY)

# ==========================================
# 🏗️ HESAPLAMA MOTORU (Manuel Okuma Modu)
# ==========================================
class RebarExtractor:
    # Daha esnek bir regex: count (opsiyonel), semboller alternation ile, ondalıklı değerlere izin
    rebar_pattern = re.compile(
        r"(?:(\d+)\s*)?(?:Ø|ø|Q|q|φ|fi|Fİ|fi)\s*(\d+(?:[.,]\d+)?)\s*(?:L\s*=\s*(\d+(?:[.,]\d+)?))?",
        re.IGNORECASE,
    )

    def parse_dxf_stream(self, file_path):
        """DXF benzeri metin tabanlı dosyalardan (TEXT/MTEXT) demir bilgisi ayıklar.
        Bu fonksiyon basit parser mantığıyla çalışır; karmaşık DXF varyantları için ezdxf önerilir.
        """
        extracted_data = []
        try:
            with open(file_path, "r", encoding="cp1252", errors="ignore") as fp:
                lines = fp.readlines()

            # DXF kod-değer çiftleri satır satır ilerler; bu yüzden indeksle ileriyoruz
            in_text_entity = False
            i = 0
            while i < len(lines) - 1:
                code_line = lines[i].strip()
                value_line = lines[i + 1].rstrip("\n")

                # İlerlemeden önce bir sonraki ikiliyi işle
                i += 2

                try:
                    code = int(code_line)
                    value = value_line.strip()
                except Exception:
                    # Eğer kod int'e dönmezse atla
                    continue

                if code == 0:
                    # Yeni entity başlıyor
                    in_text_entity = value.upper() in ("TEXT", "MTEXT")
                    continue

                # Bazı DXF varyantlarında MTEXT/TEXT için text kodu 1 veya 3 olabilir
                if in_text_entity and code in (1, 3, 7):
                    # value içinde demir bilgisi olabilir
                    match = self.rebar_pattern.search(value)
                    if match:
                        try:
                            count_raw = match.group(1)
                            diameter_raw = match.group(2)
                            length_raw = match.group(3)

                            count = int(count_raw) if count_raw else 1
                            diameter = float(diameter_raw.replace(",", ".")) if diameter_raw else None
                            length_cm = float(length_raw.replace(",", ".")) if length_raw else 0.0

                            if diameter is None:
                                continue

                            # Normalleştir: eğer çap tam sayı ise int'e düş
                            diameter_norm = int(round(diameter)) if float(diameter).is_integer() else diameter

                            extracted_data.append({
                                "raw_text": value,
                                "count": count,
                                "diameter": diameter_norm,
                                "length_cm": length_cm,
                            })
                        except Exception:
                            logger.debug("Regex eşlemesi sonrası parse hatası", exc_info=True)
                            continue

            return extracted_data
        except Exception as e:
            logger.exception("Manuel Okuma Hatası")
            return {"error": f"Dosya okuma hatası: {str(e)}"}


class MaterialCalculator:
    def __init__(self):
        # kg/metre olarak birim ağırlıklar
        self.unit_weights = {8: 0.395, 10: 0.617, 12: 0.888, 14: 1.208, 16: 1.580, 18: 2.000, 20: 2.470}
        self.stock_bar_length_m = 12.0

    def _find_closest_diameter(self, diameter):
        # Eğer verilen çap birebir yoksa, en yakın tanımlı çapa yuvarla
        try:
            dia_int = int(round(float(diameter)))
        except Exception:
            return None
        if dia_int in self.unit_weights:
            return dia_int

        # En yakın anahtarı bul
        closest = min(self.unit_weights.keys(), key=lambda k: abs(k - dia_int))
        logger.debug("Closest diameter %s for input %s", closest, diameter)
        return closest

    def calculate_needs(self, parsed_data):
        summary = {}
        for item in parsed_data:
            diameter = item.get("diameter")
            count = item.get("count", 1)
            length_cm = item.get("length_cm", 0.0)

            if diameter is None:
                continue

            closest_dia = self._find_closest_diameter(diameter)
            if closest_dia is None:
                continue

            length_m = float(length_cm) / 100.0
            total_item_length_m = length_m * int(count)

            if closest_dia not in summary:
                summary[closest_dia] = {"total_length_m": 0.0}
            summary[closest_dia]["total_length_m"] += total_item_length_m

        final_report = {}
        total_project_weight_kg = 0.0

        for dia, data in summary.items():
            total_len = data["total_length_m"]
            unit_w = self.unit_weights.get(dia, 0.0)
            weight_kg = total_len * unit_w
            stock_bars = math.ceil(total_len / self.stock_bar_length_m)

            final_report[f"Q{dia}"] = {
                "toplam_agirlik_kg": round(weight_kg, 2),
                "toplam_metraj_m": round(total_len, 2),
                "gerekli_cubuk_adet": stock_bars,
            }
            total_project_weight_kg += weight_kg

        return {
            "demir_listesi": final_report,
            # geri dönüşte her iki anahtarı da bırakıyoruz: eski ile uyumluluk için
            "toplam_agirlik_kg": round(total_project_weight_kg, 2),
            "toplam_tonaj_kg": round(total_project_weight_kg, 2),
            "okunan_veri_sayisi": len(parsed_data),
        }


# ==========================================
# ☁️ CLOUDCONVERT (presigned upload flow)
# ==========================================
def convert_dwg_to_dxf(input_path):
    try:
        logger.info("CloudConvert işlemi başlatılıyor...")

        job = cloudconvert.Job.create(payload={
            "tag": "dwg_to_dxf",
            "tasks": {
                "import-my-file": {"operation": "import/upload"},
                "convert-my-file": {"operation": "convert", "input": "import-my-file", "output_format": "dxf"},
                "export-my-file": {"operation": "export/url", "input": "convert-my-file"},
            },
        })

        logger.debug("Job Oluşturuldu: %s", json.dumps(job, indent=2, ensure_ascii=False))

        job_data = job
        if isinstance(job, dict) and "data" in job and "tasks" not in job:
            job_data = job["data"]

        if "tasks" not in job_data:
            logger.error("HATA: CloudConvert cevabında 'tasks' bulunamadı! Cevap: %s", job_data)
            return None

        # import task'ı bul
        upload_task = next((t for t in job_data["tasks"] if t.get("name") == "import-my-file"), None)
        if not upload_task:
            logger.error("Import task bulunamadı: %s", job_data.get("tasks"))
            return None

        # Eğer upload_task bize presigned form veriyorsa onu kullan
        form = upload_task.get("result", {}).get("form")
        if not form:
            # Bazı SDK sürümlerinde Task.upload fonksiyonu olabilir; deneyelim
            try:
                with open(input_path, "rb") as f:
                    cloudconvert.Task.upload(file_name=os.path.basename(input_path), task=upload_task, file=f)
            except Exception:
                logger.exception("Task.upload desteklenmiyor veya başarısız oldu ve presigned form yok.")
                return None
        else:
            # presigned form ile yükle
            url = form.get("url")
            params = form.get("parameters", {})
            with open(input_path, "rb") as f:
                files = {"file": (os.path.basename(input_path), f)}
                resp = requests.post(url, data=params, files=files)
                if not resp.ok:
                    logger.error("Presigned upload başarısız: %s - %s", resp.status_code, resp.text)
                    return None

        # Job tamamlanmasını bekle
        job = cloudconvert.Job.wait(id=job_data.get("id") or job_data.get("job", {}).get("id"))

        if isinstance(job, dict) and "data" in job and "tasks" not in job:
            job_data = job["data"]
        else:
            job_data = job

        if job_data.get("status") == "error":
            logger.error("CloudConvert Hatası: %s", json.dumps(job_data, indent=2, ensure_ascii=False))
            return None

        export_task = next((t for t in job_data.get("tasks", []) if t.get("name") == "export-my-file"), None)
        if not export_task:
            logger.error("Export task bulunamadı: %s", job_data.get("tasks"))
            return None

        if export_task.get("status") != "finished":
            logger.error("Export bitmedi: %s", export_task)
            return None

        files = export_task.get("result", {}).get("files", [])
        if not files:
            logger.error("Export sonucu dosya yok: %s", export_task)
            return None

        file_url = files[0].get("url")
        output_filename = input_path + ".dxf"
        # download
        logger.info("İndiriliyor: %s -> %s", file_url, output_filename)
        cloudconvert.download(filename=output_filename, url=file_url)

        logger.info("Dönüştürme ve indirme başarılı: %s", output_filename)
        return output_filename

    except Exception as e:
        logger.exception("Convert Hatası Detaylı")
        return None


# ==========================================
# 🌐 WEB SUNUCUSU
# ==========================================
@app.route("/", methods=["GET"])
def home():
    return "İnşaat API (CloudConvert Fix) Çalışıyor! 🏗️"


@app.route("/analiz-et", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "Dosya bulunamadı"}), 400

    file = request.files["file"]
    safe_name = secure_filename(file.filename)
    if not safe_name:
        return jsonify({"error": "Geçersiz dosya adı"}), 400

    tmp_dir = tempfile.gettempdir()
    filepath = os.path.join(tmp_dir, safe_name)
    file.save(filepath)

    target_dxf_path = filepath
    converted_file_created = False

    try:
        # DWG ise Çevir
        if safe_name.lower().endswith('.dwg'):
            logger.info("DWG tespit edildi: %s", safe_name)
            converted_path = convert_dwg_to_dxf(filepath)
            if converted_path:
                target_dxf_path = converted_path
                converted_file_created = True
            else:
                return jsonify({"error": "DWG dönüştürme başarısız (Loglara bakınız)."}), 500

        # Veriyi Çıkar
        logger.info("Analiz ediliyor: %s", target_dxf_path)
        extractor = RebarExtractor()
        raw_data = extractor.parse_dxf_stream(target_dxf_path)

        if isinstance(raw_data, dict) and "error" in raw_data:
            return jsonify(raw_data), 500

        if not raw_data:
            return jsonify({
                "error": "Dosyada okunabilir demir verisi bulunamadı.",
                "demir_listesi": {},
                "toplam_agirlik_kg": 0,
                "toplam_tonaj_kg": 0,
            }), 200

        # Hesabı Yap
        calculator = MaterialCalculator()
        result = calculator.calculate_needs(raw_data)

        return jsonify(result)

    except Exception as e:
        logger.exception("Sunucu Hatası")
        return jsonify({"error": f"Sunucu Hatası: {str(e)}"}), 500

    finally:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            if converted_file_created and target_dxf_path and os.path.exists(target_dxf_path) and target_dxf_path != filepath:
                os.remove(target_dxf_path)
            gc.collect()
        except Exception:
            logger.exception("Dosya temizleme sırasında hata")


if __name__ == '__main__':
    # debug modu ortamdan okunur, prod'da False olmalı
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
