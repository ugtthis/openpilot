#include "selfdrive/ui/qt/widgets/driving_mode_button.h"
#include "selfdrive/ui/qt/util.h"

DrivingModeButton::DrivingModeButton(QString text, DrivingMode mode, Params& params, QWidget* parent)
  : QPushButton(text, parent), mode(mode), params(params) {
  setFixedSize(925, 225);
  setStyleSheet(R"(
    QPushButton {
      font-size: 40px;
      font-weight: 500;
      border-radius: 10px;
    }
  )");
  connect(this, &QPushButton::clicked, this, &DrivingModeButton::onClicked);
  updateState();
}

void DrivingModeButton::updateState() {
  bool isEnabled = (getCurrentDrivingMode(params) == mode);
  setEnabled(!isEnabled);
  setStyleSheet(QString(R"(
    QPushButton {
      font-size: 80px;
      font-weight: 700;
      border-radius: 10px;
      background-color: %1;
      color: %2;
    }
  )").arg(isEnabled ? "#4CAF50" : "#808080", isEnabled ? "white" : "black"));
}

void DrivingModeButton::onClicked() {
  setDrivingMode(params, mode);
  updateState();
  emit drivingModeChanged();
}
