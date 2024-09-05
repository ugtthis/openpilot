#include "selfdrive/ui/qt/offroad/driving_mode_panel.h"
#include "common/params.h"

DrivingModePanel::DrivingModePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setSpacing(20);

  chillButton = new DrivingModeButton("Kirby Lose Mode", DrivingMode::Chill, params, this);
  experimentalButton = new DrivingModeButton("Experimental Mode", DrivingMode::Experimental, params, this);
  stockADASButton = new DrivingModeButton("Stock ADAS Mode", DrivingMode::StockADAS, params, this);

  layout->addWidget(chillButton);
  layout->addWidget(experimentalButton);
  layout->addWidget(stockADASButton);

  connect(chillButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);
  connect(experimentalButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);
  connect(stockADASButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);

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
