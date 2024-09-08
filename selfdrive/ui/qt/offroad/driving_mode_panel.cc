#include "selfdrive/ui/qt/offroad/driving_mode_panel.h"
#include "common/params.h"

DrivingModePanel::DrivingModePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setSpacing(20);

  auto addDrivingModeButton = [&](const QString &text, DrivingMode mode) {
    DrivingModeButton *button = new DrivingModeButton(text, mode, params, this);
    layout->addWidget(button);
    connect(button, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);
    return button;
  };

  chillButton = addDrivingModeButton("Kirby Lose Mode", DrivingMode::Chill);
  experimentalButton = addDrivingModeButton("Experimental Mode", DrivingMode::Experimental);
  stockADASButton = addDrivingModeButton("Stock ADAS Mode", DrivingMode::StockADAS);

  connect(this, &DrivingModePanel::drivingModeChanged, this, &DrivingModePanel::updateButtons);

  // Check if a mode has been set before
  if (!params.getBool("OpenpilotEnabledToggle") && !params.getBool("ExperimentalMode")) {
    // If no mode has been set, default to Chill mode
    setDrivingMode(params, DrivingMode::Chill, this);
  }

  updateButtons();

  // Create a timer to periodically check for parameter changes
  updateTimer = new QTimer(this);
  connect(updateTimer, &QTimer::timeout, this, &DrivingModePanel::checkParamsAndUpdate);
  updateTimer->start(1000);  // Check every second
}

void DrivingModePanel::updateButtons() {
  chillButton->updateState();
  experimentalButton->updateState();
  stockADASButton->updateState();
}

void DrivingModePanel::checkParamsAndUpdate() {
  static bool lastOpenpilotEnabled = params.getBool("OpenpilotEnabledToggle");
  static bool lastExperimentalMode = params.getBool("ExperimentalMode");

  bool currentOpenpilotEnabled = params.getBool("OpenpilotEnabledToggle");
  bool currentExperimentalMode = params.getBool("ExperimentalMode");

  if (currentOpenpilotEnabled != lastOpenpilotEnabled || currentExperimentalMode != lastExperimentalMode) {
    updateButtons();
    lastOpenpilotEnabled = currentOpenpilotEnabled;
    lastExperimentalMode = currentExperimentalMode;
  }
}
