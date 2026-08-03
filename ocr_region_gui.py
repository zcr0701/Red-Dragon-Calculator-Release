import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QRect, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ocr_interface import OCRInterface
from rebuild_hand import format_result, rebuild_hand_from_text
from red_dragon_calculator import (
    ETC_BAND,
    enumerate_play_paths,
    format_paths,
    state_from_rebuild_result,
)


class OCRWorker(QThread):
    result_signal = pyqtSignal(str, str, object)
    error_signal = pyqtSignal(str)

    def __init__(self, get_box_func, ocr_api):
        super().__init__()
        self.get_box_func = get_box_func
        self.ocr_api = ocr_api
        self.running = True

    def run(self):
        while self.running:
            try:
                box = self.get_box_func()

                if box is None:
                    time.sleep(0.1)
                    continue

                ocr_text = self.ocr_api.recognize_box(box)
                hand_result = rebuild_hand_from_text(ocr_text)
                hand_text = format_result(hand_result)
                self.result_signal.emit(ocr_text, hand_text, hand_result)
            except Exception as e:
                msg = f"OCR错误: {e}"
                print(msg)
                self.error_signal.emit(msg)

            time.sleep(0.1)

    def stop(self):
        self.running = False


class CalculationWorker(QThread):
    progress_signal = pyqtSignal(str)
    partial_result_signal = pyqtSignal(str)
    result_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, rebuild_result, mana_crystals, mana, max_depth=100, max_paths=500000, max_alex_count=10, min_alex_count=1, deadly_shadow_hand_indexes=None, etc_band_remaining=None):
        super().__init__()
        self.rebuild_result = rebuild_result
        self.mana_crystals = mana_crystals
        self.mana = mana
        self.max_depth = max_depth
        self.max_paths = max_paths
        self.max_alex_count = max_alex_count
        self.min_alex_count = min_alex_count
        self.deadly_shadow_hand_indexes = deadly_shadow_hand_indexes or []
        self.etc_band_remaining = list(etc_band_remaining) if etc_band_remaining is not None else ETC_BAND[:]

    def format_initial_state_note(self, state):
        def card_cost_health_text(card):
            cost = card.current_cost()
            cost_text = "*" if cost is None else str(cost)
            health_text = f"{card.health}血" if getattr(card, "card_type", "") == "minion" and getattr(card, "health", None) is not None else ""

            if health_text:
                return f"{cost_text}费,{health_text}"

            return f"{cost_text}费"

        lines = [
            f"计算参数：{self.mana_crystals}水晶 / {self.mana}法力 / 链条步数上限 {self.max_depth} / 路径上限 {self.max_paths} / 搜索龙数上限 {self.max_alex_count} / 搜索龙数下限 {self.min_alex_count}",
        ]
        marked_cards = []

        for index, card in enumerate(state.hand, start=1):
            if card.is_deadly_shadow:
                marked_cards.append(f"第{index}张：{card.name}[殒命暗影]")

        if marked_cards:
            lines.append("殒命暗影标记已生效：" + "；".join(marked_cards))
        else:
            lines.append("殒命暗影标记：未生效（没有手牌被标记为殒命暗影）")

        if getattr(state, "log", None):
            initial_effects = [item for item in state.log if item.startswith("初始当前效果：")]
            if initial_effects:
                lines.append("当前效果已生效：" + "；".join(item.replace("初始当前效果：", "") for item in initial_effects))
            else:
                lines.append("当前效果：无")
        else:
            lines.append("当前效果：无")

        lines.append(
            "初始手牌："
            + "，".join(
                f"{index}.{card.name}{'[殒命暗影]' if card.is_deadly_shadow else ''}[{card_cost_health_text(card)}]"
                for index, card in enumerate(state.hand, start=1)
            )
        )
        lines.append(
            "初始战场："
            + ("，".join(f"{index}.{card.name}[{card_cost_health_text(card)}]" for index, card in enumerate(state.board, start=1)) or "空")
        )
        lines.append(
            "初始奥秘："
            + ("，".join(f"{index}.{card.name}[{card_cost_health_text(card)}]" for index, card in enumerate(state.secrets, start=1)) or "空")
        )
        lines.append("初始武器：" + (f"{state.weapon.name}[{card_cost_health_text(state.weapon)}]" if state.weapon else "空"))
        lines.append("牛头人酋长剩余卡池：" + ("，".join(state.etc_band_remaining) if state.etc_band_remaining else "空"))
        lines.append("")
        return "\n".join(lines)

    def format_prune_stats(self, prune_stats):
        if not prune_stats:
            return "符号链条证明统计：暂无记录\n"

        meta_keys = {"当前证明目标", "已证明龙数"}
        regular_stats = {
            key: value
            for key, value in prune_stats.items()
            if key not in meta_keys and not str(key).startswith("当前搜索 ") and isinstance(value, int)
            and not str(key).startswith("链条失败详情：")
        }
        failure_details = {
            key: value
            for key, value in prune_stats.items()
            if isinstance(value, int) and str(key).startswith("链条失败详情：")
        }
        lines = ["符号链条证明统计："]

        if "已证明龙数" in prune_stats:
            lines.append(f"- 已证明龙数：{prune_stats['已证明龙数']}")
        elif "当前证明目标" in prune_stats:
            lines.append(f"- 当前证明目标：{prune_stats['当前证明目标']}龙")

        for key, value in prune_stats.items():
            if str(key).startswith("当前搜索 "):
                lines.append(f"- {key}：{value}")

        if "符号候选链条数" in regular_stats:
            lines.append(f"- 符号候选链条数：{regular_stats.pop('符号候选链条数')}")

        if "符号链条DP复用" in regular_stats:
            lines.append(f"- 符号链条DP复用：{regular_stats.pop('符号链条DP复用')}")

        for reason, count in sorted(regular_stats.items(), key=lambda item: -item[1])[:30]:
            lines.append(f"- {reason}：{count}")

        if failure_details:
            lines.append("- 关键失败现场（最多显示20条）：")

            for reason, count in sorted(failure_details.items(), key=lambda item: -item[1])[:20]:
                lines.append(f"- {reason.replace('链条失败详情：', '')}：{count}")

        lines.append("")
        return "\n".join(lines)

    def export_all_paths_document(self, state, states, prune_stats, stop_note, limit_note):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = Path(__file__).resolve().parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        output_path = logs_dir / f"red_dragon_all_paths_{timestamp}.txt"
        header = (
            self.format_initial_state_note(state)
            + self.format_prune_stats(prune_stats)
            + stop_note
            + limit_note
            + "以下为全部路径：\n\n"
        )
        body = format_paths(
            states,
            limit=max(1, len(states)),
            non_alex_limit=max(1, len(states))
        )

        output_path.write_text(header + body, encoding="utf-8")
        return output_path

    def run(self):
        try:
            state = state_from_rebuild_result(
                result=self.rebuild_result,
                mana_crystals=self.mana_crystals,
                mana=self.mana,
                deadly_shadow_hand_indexes=self.deadly_shadow_hand_indexes,
                etc_band_remaining=self.etc_band_remaining
            )

            def on_progress(done_count, stack_count, pruned_count=0):
                self.progress_signal.emit(
                    f"正在符号化证明路径：已找到 {done_count} 条目标路径，剩余候选链 {stack_count} 条，复用/失败记录 {pruned_count} 条..."
                )

            def on_found(found_states, target_alex_count):
                self.partial_result_signal.emit(
                    self.format_initial_state_note(state)
                    + self.format_prune_stats(prune_stats)
                    + f"已即时发现 {len(found_states)} 条 {target_alex_count} 龙路径，仍在继续计算剩余链条；可点击“中止计算”立即导出当前全部结果。\n\n"
                    + format_paths(found_states, limit=300)
                )

            prune_stats = {}
            states = enumerate_play_paths(
                initial_state=state,
                max_depth=self.max_depth,
                max_paths=self.max_paths,
                max_alex_count=self.max_alex_count,
                min_alex_count=self.min_alex_count,
                progress_callback=on_progress,
                found_callback=on_found,
                prune_stats=prune_stats,
                should_stop=self.isInterruptionRequested
            )
            limit_note = ""

            if len(states) >= self.max_paths:
                limit_note = f"注意：本次结果达到路径上限 {self.max_paths}，可能还没有完整算完；可继续调高路径上限。\n"

            stop_note = ""

            if self.isInterruptionRequested():
                stop_note = "已中止计算，以下为中止前已经算出的部分结果。\n"

            export_path = self.export_all_paths_document(
                state=state,
                states=states,
                prune_stats=prune_stats,
                stop_note=stop_note,
                limit_note=limit_note
            )
            export_note = f"完整路径文档已生成：{export_path}\n"

            self.result_signal.emit(
                self.format_initial_state_note(state)
                + self.format_prune_stats(prune_stats)
                + stop_note
                + limit_note
                + export_note
                + format_paths(states, limit=300)
            )
        except Exception as e:
            self.error_signal.emit(f"计算错误: {e}")


class SelectionOverlay(QWidget):
    selected_signal = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR区域选择")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.start = None
        self.end = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start = event.pos()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event):
        if self.start:
            self.end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None:
            return

        self.end = event.pos()
        x1 = min(self.start.x(), self.end.x())
        y1 = min(self.start.y(), self.end.y())
        x2 = max(self.start.x(), self.end.x())
        y2 = max(self.start.y(), self.end.y())

        self.start = None
        self.end = None

        if x2 - x1 > 20 and y2 - y1 > 20:
            box = (x1, y1, x2, y2)
            print("选择区域:", box)
            self.selected_signal.emit(box)

        self.hide()
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self.start and self.end:
            selected_rect = QRect(self.start, self.end).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(selected_rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(Qt.red, 3))
            painter.drawRect(selected_rect)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR识别与手牌重建")
        self.setWindowFlags(Qt.WindowStaysOnTopHint)

        self.box = None
        self.worker = None
        self.calc_worker = None
        self.latest_rebuild_result = None
        self.ocr_api = OCRInterface(lang="ch")

        self.overlay = SelectionOverlay()
        self.overlay.selected_signal.connect(self.on_area_selected)

        self.statusLabel = QLabel("状态：请先点击“框选区域”")
        self.statusLabel.setStyleSheet("font-size:16px;font-family:微软雅黑;")

        self.selectButton = QPushButton("框选区域")
        self.startButton = QPushButton("开始识别")
        self.stopButton = QPushButton("停止识别")
        self.calculateButton = QPushButton("开始计算")
        self.cancelCalculationButton = QPushButton("中止计算")
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(False)
        self.calculateButton.setEnabled(False)
        self.cancelCalculationButton.setEnabled(False)

        self.selectButton.clicked.connect(self.open_selector)
        self.startButton.clicked.connect(self.start_ocr)
        self.stopButton.clicked.connect(self.stop_ocr)
        self.calculateButton.clicked.connect(self.start_calculation)
        self.cancelCalculationButton.clicked.connect(self.cancel_calculation)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.selectButton)
        button_layout.addWidget(self.startButton)
        button_layout.addWidget(self.stopButton)
        button_layout.addWidget(self.calculateButton)
        button_layout.addWidget(self.cancelCalculationButton)

        self.crystalInput = QLineEdit("4")
        self.crystalInput.setFixedWidth(60)
        self.manaInput = QLineEdit("8")
        self.manaInput.setFixedWidth(60)
        self.maxDepthInput = QLineEdit("100")
        self.maxDepthInput.setFixedWidth(70)
        self.maxPathsInput = QLineEdit("500000")
        self.maxPathsInput.setFixedWidth(90)
        self.maxAlexInput = QLineEdit("10")
        self.maxAlexInput.setFixedWidth(60)
        self.minAlexInput = QLineEdit("1")
        self.minAlexInput.setFixedWidth(60)

        mana_layout = QHBoxLayout()
        mana_layout.addWidget(QLabel("水晶："))
        mana_layout.addWidget(self.crystalInput)
        mana_layout.addWidget(QLabel("法力："))
        mana_layout.addWidget(self.manaInput)
        mana_layout.addWidget(QLabel("链条步数上限："))
        mana_layout.addWidget(self.maxDepthInput)
        mana_layout.addWidget(QLabel("路径上限："))
        mana_layout.addWidget(self.maxPathsInput)
        mana_layout.addWidget(QLabel("搜索龙数上限："))
        mana_layout.addWidget(self.maxAlexInput)
        mana_layout.addWidget(QLabel("搜索龙数下限："))
        mana_layout.addWidget(self.minAlexInput)
        mana_layout.addStretch()

        self.deadlyShadowCheck = QCheckBox("标记殒命暗影")
        self.deadlyShadowInput = QLineEdit()
        self.deadlyShadowInput.setPlaceholderText("手牌序号，如 3 或 3,7")
        self.deadlyShadowInput.setFixedWidth(160)
        self.deadlyShadowInput.setEnabled(False)
        self.deadlyShadowCheck.toggled.connect(self.deadlyShadowInput.setEnabled)

        deadly_shadow_layout = QHBoxLayout()
        deadly_shadow_layout.addWidget(self.deadlyShadowCheck)
        deadly_shadow_layout.addWidget(QLabel("殒命暗影位置："))
        deadly_shadow_layout.addWidget(self.deadlyShadowInput)
        deadly_shadow_layout.addWidget(QLabel("按重建手牌列表从 1 开始编号"))
        deadly_shadow_layout.addStretch()

        self.etcDanceCheck = QCheckBox("舞动全场（ft.迦罗娜）")
        self.etcPotionCheck = QCheckBox("幻觉药水")
        self.etcAlexCheck = QCheckBox("生命的缚誓者阿莱克丝塔萨")
        for checkbox in [self.etcDanceCheck, self.etcPotionCheck, self.etcAlexCheck]:
            checkbox.setChecked(True)

        etc_layout = QHBoxLayout()
        etc_layout.addWidget(QLabel("牛头人酋长剩余卡池："))
        etc_layout.addWidget(self.etcDanceCheck)
        etc_layout.addWidget(self.etcPotionCheck)
        etc_layout.addWidget(self.etcAlexCheck)
        etc_layout.addWidget(QLabel("取消勾选表示这张已经被选走"))
        etc_layout.addStretch()

        self.ocrText = QTextEdit()
        self.ocrText.setReadOnly(True)
        self.handText = QTextEdit()
        self.handText.setReadOnly(True)
        self.calcText = QTextEdit()
        self.calcText.setReadOnly(True)

        text_style = """
        QTextEdit{
            color:red;
            background:white;
            font-size:22px;
            font-family:微软雅黑;
            padding:10px;
            border:1px solid #ddd;
            border-radius:8px;
        }
        """
        self.ocrText.setStyleSheet(text_style)
        self.handText.setStyleSheet(text_style)
        self.calcText.setStyleSheet(text_style)

        ocr_panel = QWidget()
        ocr_layout = QVBoxLayout()
        ocr_layout.addWidget(QLabel("OCR识别文本"))
        ocr_layout.addWidget(self.ocrText)
        ocr_panel.setLayout(ocr_layout)

        hand_panel = QWidget()
        hand_layout = QVBoxLayout()
        hand_layout.addWidget(QLabel("重建牌库与手牌"))
        hand_layout.addWidget(self.handText)
        hand_panel.setLayout(hand_layout)

        calc_panel = QWidget()
        calc_layout = QVBoxLayout()
        calc_layout.addWidget(QLabel("出牌路径计算"))
        calc_layout.addWidget(self.calcText)
        calc_panel.setLayout(calc_layout)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(ocr_panel)
        splitter.addWidget(hand_panel)
        splitter.addWidget(calc_panel)
        splitter.setSizes([220, 320, 320])

        layout = QVBoxLayout()
        layout.addWidget(self.statusLabel)
        layout.addLayout(button_layout)
        layout.addLayout(mana_layout)
        layout.addLayout(deadly_shadow_layout)
        layout.addLayout(etc_layout)
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.resize(760, 880)
        self.move(180, 80)
        self.setResult("识别结果会显示在这里", "重建后的牌库与手牌会显示在这里", None)
        self.calcText.setPlainText("计算结果会显示在这里")

    def setResult(self, ocr_text, hand_text, rebuild_result=None):
        self.ocrText.setPlainText(ocr_text if ocr_text else "未识别到文字")
        self.handText.setPlainText(hand_text if hand_text else "暂无重建结果")

        if rebuild_result is not None:
            self.latest_rebuild_result = rebuild_result
            self.calculateButton.setEnabled(True)

    def open_selector(self):
        if self.worker is not None:
            QMessageBox.information(self, "提示", "请先停止识别，再重新框选区域。")
            return

        self.statusLabel.setText("状态：拖拽鼠标框选需要 OCR 的区域，按 Esc 可取消")
        self.overlay.showFullScreen()
        self.overlay.raise_()
        self.overlay.activateWindow()

    def on_area_selected(self, box):
        self.box = box
        self.startButton.setEnabled(True)
        self.statusLabel.setText(f"状态：已选择区域 {box}，点击“开始识别”")

    def get_box(self):
        return self.box

    def get_deadly_shadow_hand_indexes(self):
        if not self.deadlyShadowCheck.isChecked():
            return []

        text = self.deadlyShadowInput.text().strip()

        if not text:
            raise ValueError("启用殒命暗影标记后，请输入手牌序号。")

        parts = (
            text.replace("，", ",")
            .replace("、", ",")
            .replace(" ", ",")
            .split(",")
        )
        indexes = []

        for part in parts:
            item = part.strip()

            if not item:
                continue

            if not item.isdigit():
                raise ValueError("殒命暗影位置只能填写数字，例如 3 或 3,7。")

            indexes.append(int(item))

        if not indexes:
            raise ValueError("启用殒命暗影标记后，请输入手牌序号。")

        hand_count = len(getattr(self.latest_rebuild_result, "cards", []))

        for index in indexes:
            if index <= 0:
                raise ValueError("殒命暗影位置必须从 1 开始。")

            if index > hand_count:
                raise ValueError(f"殒命暗影位置第 {index} 张超出当前手牌数量 {hand_count}。")

        return indexes

    def get_etc_band_remaining(self):
        selected = []
        pairs = [
            (self.etcDanceCheck, "舞动全场（ft.迦罗娜）"),
            (self.etcPotionCheck, "幻觉药水"),
            (self.etcAlexCheck, "生命的缚誓者阿莱克丝塔萨"),
        ]

        for checkbox, card_name in pairs:
            if checkbox.isChecked():
                selected.append(card_name)

        return selected

    def start_ocr(self):
        if self.box is None:
            QMessageBox.warning(self, "提示", "请先点击“框选区域”选择 OCR 区域。")
            return

        if self.worker is not None:
            return

        self.worker = OCRWorker(self.get_box, self.ocr_api)
        self.worker.result_signal.connect(self.setResult)
        self.worker.error_signal.connect(self.on_ocr_error)
        self.worker.start()

        self.statusLabel.setText("状态：正在截图、识别并重建手牌")
        self.selectButton.setEnabled(False)
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)

    def start_calculation(self):
        if self.latest_rebuild_result is None:
            QMessageBox.warning(self, "提示", "请先完成一次识别，得到牌库和手牌。")
            return

        if self.calc_worker is not None:
            QMessageBox.information(self, "提示", "当前正在计算，请稍等。")
            return

        try:
            mana_crystals = int(self.crystalInput.text().strip())
            mana = int(self.manaInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "水晶和法力必须是数字。")
            return

        try:
            max_depth = int(self.maxDepthInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "链条步数上限必须是数字。")
            return

        if max_depth <= 0:
            QMessageBox.warning(self, "提示", "链条步数上限必须大于 0。")
            return

        try:
            max_paths = int(self.maxPathsInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "路径上限必须是数字。")
            return

        if max_paths <= 0:
            QMessageBox.warning(self, "提示", "路径上限必须大于 0。")
            return

        try:
            max_alex_count = int(self.maxAlexInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "搜索龙数上限必须是数字。")
            return

        if max_alex_count <= 0:
            QMessageBox.warning(self, "提示", "搜索龙数上限必须大于 0。")
            return

        try:
            min_alex_count = int(self.minAlexInput.text().strip())
        except ValueError:
            QMessageBox.warning(self, "提示", "搜索龙数下限必须是数字。")
            return

        if min_alex_count <= 0:
            QMessageBox.warning(self, "提示", "搜索龙数下限必须大于 0。")
            return

        if min_alex_count > max_alex_count:
            QMessageBox.warning(self, "提示", "搜索龙数下限不能大于上限。")
            return

        try:
            deadly_shadow_hand_indexes = self.get_deadly_shadow_hand_indexes()
        except ValueError as e:
            QMessageBox.warning(self, "提示", str(e))
            return

        self.calcText.setPlainText("正在计算所有可行出牌路径...")
        self.calculateButton.setEnabled(False)
        self.cancelCalculationButton.setEnabled(True)

        self.calc_worker = CalculationWorker(
            rebuild_result=self.latest_rebuild_result,
            mana_crystals=mana_crystals,
            mana=mana,
            max_depth=max_depth,
            max_paths=max_paths,
            max_alex_count=max_alex_count,
            min_alex_count=min_alex_count,
            deadly_shadow_hand_indexes=deadly_shadow_hand_indexes,
            etc_band_remaining=self.get_etc_band_remaining()
        )
        self.calc_worker.progress_signal.connect(self.on_calculation_progress)
        self.calc_worker.partial_result_signal.connect(self.on_calculation_partial)
        self.calc_worker.result_signal.connect(self.on_calculation_finished)
        self.calc_worker.error_signal.connect(self.on_calculation_error)
        self.calc_worker.finished.connect(self.on_calculation_thread_finished)
        self.calc_worker.start()

    def cancel_calculation(self):
        if self.calc_worker is None:
            return

        self.calcText.append("\n正在中止计算，将展示并导出已经算出的全部结果...")
        self.cancelCalculationButton.setEnabled(False)
        self.calc_worker.requestInterruption()

    def on_calculation_progress(self, text):
        self.statusLabel.setText("状态：" + text)

    def on_calculation_partial(self, text):
        self.calcText.setPlainText(text)

    def on_calculation_finished(self, text):
        self.calcText.setPlainText(text)
        self.statusLabel.setText("状态：计算完成，完整路径文档已生成")

    def on_calculation_error(self, msg):
        self.calcText.setPlainText(msg)
        self.statusLabel.setText("状态：计算失败")

    def on_calculation_thread_finished(self):
        self.calc_worker = None
        self.calculateButton.setEnabled(self.latest_rebuild_result is not None)
        self.cancelCalculationButton.setEnabled(False)

    def stop_ocr(self):
        if self.worker is None:
            return

        self.worker.stop()
        self.worker.wait()
        self.worker = None

        self.statusLabel.setText("状态：已停止识别，可重新框选区域")
        self.selectButton.setEnabled(True)
        self.startButton.setEnabled(self.box is not None)
        self.stopButton.setEnabled(False)

    def on_ocr_error(self, msg):
        self.statusLabel.setText(msg)

    def closeEvent(self, event):
        self.stop_ocr()
        if self.calc_worker is not None:
            self.calc_worker.requestInterruption()
            self.calc_worker.wait()
        self.overlay.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = MainWindow()
    gui.show()
    sys.exit(app.exec_())
