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
  QString textColor = isEnabled ? "white" : "#555555";

  const double disabledButtonOpacity = 0.2;

  if (isEnabled) {
    switch (mode) {
      case DrivingMode::StockADAS:
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                          "stop:0 #1f1c18, stop:0.33 #3e3e3e, "
                          "stop:0.66 #5a5454, stop:1 #8e9eab)";
        break;
      case DrivingMode::Chill:
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                          "stop:0 #00c88c, stop:1 #0077be)";
        break;
      case DrivingMode::Experimental:
        backgroundColor = "qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                          "stop:0 #ff8c2f, stop:1 #c62a1d)";
        break;
    }
  } else {
    switch (mode) {
      case DrivingMode::StockADAS:
        backgroundColor = QString("qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                                  "stop:0 rgba(31,28,24,%1), stop:0.33 rgba(62,62,62,%1), "
                                  "stop:0.66 rgba(90,84,84,%1), stop:1 rgba(142,158,171,%1))")
                                  .arg(disabledButtonOpacity * 255);
        break;
      case DrivingMode::Chill:
        backgroundColor = QString("qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                                  "stop:0 rgba(0,200,140,%1), stop:1 rgba(0,119,190,%1))")
                                  .arg(disabledButtonOpacity * 255);
        break;
      case DrivingMode::Experimental:
        backgroundColor = QString("qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                                  "stop:0 rgba(255,140,47,%1), stop:1 rgba(198,42,29,%1))")
                                  .arg(disabledButtonOpacity * 255);
        break;
    }
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
