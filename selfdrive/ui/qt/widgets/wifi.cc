#include "selfdrive/ui/qt/widgets/wifi.h"

#include <QHBoxLayout>
#include <QLabel>
#include <QPixmap>
#include <QPushButton>

WiFiPromptWidget::WiFiPromptWidget(QWidget *parent) : QFrame(parent) {
  setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);

  stack = new QStackedLayout(this);

  // Setup Wi-Fi
  QFrame *setup = new QFrame;
  QVBoxLayout *setup_layout = new QVBoxLayout(setup);
  setup_layout->setContentsMargins(56, 60, 56, 40);
  setup_layout->setSpacing(45);
  {
    QLabel *icon = new QLabel;
    QPixmap pixmap("../assets/offroad/icon_wifi_none.svg");
    icon->setPixmap(pixmap.scaledToWidth(225, Qt::SmoothTransformation));
    icon->setAlignment(Qt::AlignCenter);
    setup_layout->addWidget(icon);

    setup_layout->addStretch();

    QLabel *desc = new QLabel(tr("Connect to Wi-Fi to upload driving data and help improve openpilot"));
    desc->setStyleSheet("font-size: 40px; font-weight: 400; padding-left: 50px; padding-right: 50px;");
    desc->setWordWrap(true);
    desc->setAlignment(Qt::AlignCenter);
    setup_layout->addWidget(desc);

    setup_layout->addStretch();

    QPushButton *settings_btn = new QPushButton(tr("Setup Wi-Fi"));
    connect(settings_btn, &QPushButton::clicked, [=]() { emit openSettings(1); });
    settings_btn->setStyleSheet(R"(
      QPushButton {
        font-size: 48px;
        font-weight: 500;
        border-radius: 20px;
        background-color: #465BEA;
        padding: 32px;
      }
      QPushButton:pressed {
        background-color: #3049F4;
      }
    )");
    setup_layout->addWidget(settings_btn);
  }
  stack->addWidget(setup);

  // Uploading data
  QWidget *uploading = new QWidget;
  QVBoxLayout *uploading_layout = new QVBoxLayout(uploading);
  uploading_layout->setContentsMargins(50, 60, 50, 60);
  uploading_layout->setSpacing(0); // Without this WiFi and Connected labels are too far apart
  QLabel *icon = new QLabel;
  QPixmap pixmap("../assets/offroad/icon_wifi.svg");
  icon->setPixmap(pixmap.scaledToWidth(200, Qt::SmoothTransformation));
  icon->setAlignment(Qt::AlignCenter);
  uploading_layout->addWidget(icon);

  uploading_layout->addStretch();

  QLabel *top_label = new QLabel(tr("Wi-Fi"));
  top_label->setStyleSheet("font-size: 58px; font-weight: 400;");
  top_label->setAlignment(Qt::AlignCenter);
  uploading_layout->addWidget(top_label);

  QLabel *bottom_label = new QLabel(tr("Connected"));
  bottom_label->setStyleSheet("font-size: 77px; font-weight: 600; color: #AAED70;");
  bottom_label->setAlignment(Qt::AlignCenter);
  uploading_layout->addWidget(bottom_label);

  uploading_layout->addStretch();

  QLabel *desc = new QLabel(tr("Training data will be pulled periodically while your device is on Wi-Fi"));
  desc->setStyleSheet("font-size: 42px; font-weight: 400;");
  desc->setWordWrap(true);
  desc->setAlignment(Qt::AlignCenter);
  uploading_layout->addWidget(desc);

  stack->addWidget(uploading);

  setStyleSheet(R"(
    WiFiPromptWidget {
      background-color: #333333;
      border-radius: 25px;
    }
  )");

  QObject::connect(uiState(), &UIState::uiUpdate, this, &WiFiPromptWidget::updateState);
}

void WiFiPromptWidget::updateState(const UIState &s) {
  if (!isVisible()) return;

  auto &sm = *(s.sm);

  auto network_type = sm["deviceState"].getDeviceState().getNetworkType();
  auto uploading = network_type == cereal::DeviceState::NetworkType::WIFI ||
      network_type == cereal::DeviceState::NetworkType::ETHERNET;
  stack->setCurrentIndex(uploading ? 1 : 1);
}
