#include "driving_mode_info_dialog.h"
#include <QGuiApplication>
#include <QScreen>

DrivingModeInfoDialog::DrivingModeInfoDialog(DrivingMode mode, QWidget *parent)
    : QWidget(parent) {
  setupUI();
  setModeInfo(mode);
  hide(); // Initially hidden

  setStyleSheet(R"(
    DrivingModeInfoDialog {
      background-color: #333333;
      border-radius: 10px;
    }
    QLabel { color: white; }
    #iconLabel { qproperty-alignment: AlignCenter; }
    #titleLabel { font-size: 50px; font-weight: bold; }
    #contentLabel { font-size: 40px; }
    QPushButton {
      font-size: 40px;
      padding: 20px;
      border-radius: 10px;
    }
    #cancelButton {
      background-color: #444444;
      color: white;
    }
    #enableButton {
      background-color: #4CAF50;
      color: white;
    }
  )");

  setAttribute(Qt::WA_StyledBackground, true);
}

void DrivingModeInfoDialog::show(DrivingMode mode) {
  setModeInfo(mode);
  QWidget::show();
}

void DrivingModeInfoDialog::setupUI() {
  QVBoxLayout *mainLayout = new QVBoxLayout(this);
  mainLayout->setContentsMargins(50, 50, 50, 50);
  mainLayout->setSpacing(30);

  iconLabel = new QLabel(this);
  iconLabel->setObjectName("iconLabel");
  iconLabel->setFixedSize(100, 100);
  mainLayout->addWidget(iconLabel);

  titleLabel = new QLabel(this);
  titleLabel->setObjectName("titleLabel");
  mainLayout->addWidget(titleLabel);

  contentLabel = new QLabel(this);
  contentLabel->setObjectName("contentLabel");
  contentLabel->setWordWrap(true);
  mainLayout->addWidget(contentLabel);

  mainLayout->addStretch();

  QHBoxLayout *buttonLayout = new QHBoxLayout();
  cancelButton = new QPushButton(tr("Cancel"), this);
  cancelButton->setObjectName("cancelButton");
  enableButton = new QPushButton(tr("Enable"), this);
  enableButton->setObjectName("enableButton");

  buttonLayout->addWidget(cancelButton);
  buttonLayout->addWidget(enableButton);

  mainLayout->addLayout(buttonLayout);

  connect(cancelButton, &QPushButton::clicked, this, &DrivingModeInfoDialog::rejected);
  connect(enableButton, &QPushButton::clicked, this, &DrivingModeInfoDialog::accepted);
}

void DrivingModeInfoDialog::setModeInfo(DrivingMode mode) {
  switch (mode) {
    case DrivingMode::Chill:
      iconLabel->setPixmap(QPixmap("../assets/img_chffr_wheel.png").scaled(100, 100, Qt::KeepAspectRatio, Qt::SmoothTransformation));
      titleLabel->setText(tr("Chill Mode"));
      contentLabel->setText(tr("This is the default mode. It's safe and reliable for everyday driving. "
                               "The system will be more conservative in its actions, prioritizing a smooth and comfortable ride."));
      break;
    case DrivingMode::Experimental:
      iconLabel->setPixmap(QPixmap("../assets/img_experimental.svg").scaled(100, 100, Qt::KeepAspectRatio, Qt::SmoothTransformation));
      titleLabel->setText(tr("Experimental Mode"));
      contentLabel->setText(tr("Use the openpilot system for adaptive cruise control and lane keep driver assistance. "
                               "Your attention is required at all times to use this feature. Changing this setting takes effect "
                               "when the car is powered off."));
      break;
    case DrivingMode::StockADAS:
      iconLabel->setPixmap(QPixmap());
      titleLabel->setText(tr("Stock ADAS Mode"));
      contentLabel->setText(tr("This mode uses the stock ADAS features of your vehicle. It provides a familiar driving experience "
                               "with the safety features you're accustomed to."));
      break;
  }
}