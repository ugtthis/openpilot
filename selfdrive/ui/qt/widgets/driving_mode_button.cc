#include "selfdrive/ui/qt/widgets/driving_mode_button.h"
#include "selfdrive/ui/qt/widgets/driving_mode_helpers.h"
#include "selfdrive/ui/qt/util.h"

#include <QVBoxLayout>
#include <QLabel>
#include <QHBoxLayout>

DrivingModeButton::DrivingModeButton(const QString &text, DrivingMode mode, Params &params, QWidget *parent)
    : QPushButton(parent), mode(mode), params(params) {
  setFixedHeight(235);
  // Allow horizontal stretching
  setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);

  // Set content margins to align text to top-left and adds 50px right padding
  setContentsMargins(10, 10, 50, 10);

  QHBoxLayout *mainLayout = new QHBoxLayout(this);
  mainLayout->setContentsMargins(0, 0, 0, 0);

  QVBoxLayout *textLayout = new QVBoxLayout();
  textLayout->setAlignment(Qt::AlignLeft | Qt::AlignVCenter);

  QLabel *textLabel = new QLabel(text, this);
  textLabel->setAlignment(Qt::AlignLeft | Qt::AlignTop);
  textLayout->addWidget(textLabel);

  textLayout->addStretch();

  mainLayout->addLayout(textLayout);
  mainLayout->addStretch();

  statusCircle = new QLabel(this);
  statusCircle->setFixedSize(76, 76);
  statusCircle->setStyleSheet("border: 16px solid black; border-radius: 38px; background-color: transparent;");
  mainLayout->addWidget(statusCircle, 0, Qt::AlignVCenter);

  setLayout(mainLayout);

  connect(this, &QPushButton::clicked, this, &DrivingModeButton::onClicked);
  updateState();
}

void DrivingModeButton::updateState() {
  bool isEnabled = (getCurrentDrivingMode(params) == mode);
  setEnabled(!isEnabled);

  QString backgroundColor;
  QString textColor = isEnabled ? "white" : "black";

  // TODO: Organize this so it also reflects the order of the modes in the enum and device panel
  if (isEnabled) {
    switch (mode) {
      case DrivingMode::StockADAS:
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #ff7e5f, stop:1 #feb47b)";
        break;
      case DrivingMode::Chill:
        // backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #14ffab, stop:1 #2395ff)";
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #00c88c, stop:1 #0077be)";
        break;
      case DrivingMode::Experimental:
        // backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #ff9b3f, stop:1 #db3822)";
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #ff8c2f, stop:1 #c62a1d)";
        break;
    }
  } else {
    backgroundColor = "#808080";
  }

  QString styleSheet = QString(R"(
    QPushButton {
      border-radius: 10px;
      background: %1;
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
  )").arg(backgroundColor, textColor);

  setStyleSheet(styleSheet);
  updateCircle();
}

void DrivingModeButton::updateCircle() {
  bool isSelected = (getCurrentDrivingMode(params) == mode);
  if (isSelected) {
    statusCircle->setStyleSheet("border: 16px solid black; border-radius: 38px; background-color: #00FF00;");
  } else {
    statusCircle->setStyleSheet("border: 16px solid black; border-radius: 38px; background-color: transparent;");
  }
}

void DrivingModeButton::onClicked() {
  setDrivingMode(params, mode);
  updateState();
  emit drivingModeChanged();
}
