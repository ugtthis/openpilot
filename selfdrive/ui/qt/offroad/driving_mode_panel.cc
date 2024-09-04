#include "selfdrive/ui/qt/offroad/driving_mode_panel.h"
#include "common/params.h"

DrivingModePanel::DrivingModePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setSpacing(20);

  chillButton = new DrivingModeButton("Chill Mode", DrivingMode::Chill, this);
  experimentalButton = new DrivingModeButton("Experimental Mode", DrivingMode::Experimental, this);
  stockADASButton = new DrivingModeButton("Stock ADAS Mode", DrivingMode::StockADAS, this);

  layout->addWidget(chillButton);
  layout->addWidget(experimentalButton);
  layout->addWidget(stockADASButton);

  connect(chillButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);
  connect(experimentalButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);
  connect(stockADASButton, &DrivingModeButton::drivingModeChanged, this, &DrivingModePanel::updateButtons);

  // Check if a mode has been set before
  Params params;
  if (!params.getBool("OpenpilotEnabledToggle") && !params.getBool("ExperimentalMode")) {
    // If no mode has been set, default to Chill mode
    setDrivingMode(DrivingMode::Chill);
  }

  updateButtons();
}

void DrivingModePanel::updateButtons() {
  chillButton->updateState();
  experimentalButton->updateState();
  stockADASButton->updateState();
}
