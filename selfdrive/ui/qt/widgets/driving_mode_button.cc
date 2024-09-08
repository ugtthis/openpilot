#include "selfdrive/ui/qt/widgets/driving_mode_button.h"
#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"
#include "selfdrive/ui/qt/util.h"

#include <QVBoxLayout>
#include <QLabel>

DrivingModeButton::DrivingModeButton(QString text, DrivingMode mode, Params& params, QWidget* parent)
  : QPushButton(parent), mode(mode), params(params) {
  setFixedHeight(225);
  // Allow horizontal stretching
  setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);

  // Set content margins to align text to top-left
  setContentsMargins(10, 10, 10, 10);

  // Create a layout for the button
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setAlignment(Qt::AlignLeft | Qt::AlignTop);

  // Create a label for the text
  QLabel* textLabel = new QLabel(text, this);
  textLabel->setAlignment(Qt::AlignLeft | Qt::AlignTop);
  layout->addWidget(textLabel);

  // Add stretch to push the label to the top
  layout->addStretch();

  setLayout(layout);

  connect(this, &QPushButton::clicked, this, &DrivingModeButton::onClicked);
  updateState();
}

void DrivingModeButton::updateState() {
  bool isEnabled = (getCurrentDrivingMode(params) == mode);
  setEnabled(!isEnabled);

  QString styleSheet = QString(R"(
    QPushButton {
      border-radius: 10px;
      background-color: %1;
      padding: 0px;
    }
    QLabel {
      font-size: 70px;
      font-weight: 700;
      color: %2;
      background-color: rgba(0, 0, 0, 0);
      border-radius: 5px;
      padding: 5px;
    }
  )").arg(isEnabled ? "#4CAF50" : "#808080", isEnabled ? "white" : "black");

  setStyleSheet(styleSheet);
}

void DrivingModeButton::onClicked() {
  setDrivingMode(params, mode);
  updateState();
  emit drivingModeChanged();
}
