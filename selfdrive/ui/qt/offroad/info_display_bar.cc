#include "selfdrive/ui/qt/offroad/info_display_bar.h"
#include <QPixmap>
#include <QPainter>

InfoDisplayBar::InfoDisplayBar(QWidget* parent) : QWidget(parent) {
  layout = new QHBoxLayout(this);
  layout->setContentsMargins(10, 10, 10, 10); // Adjust margins for border padding
  layout->setSpacing(10);

  iconLabel = new QLabel(this);
  iconLabel->setFixedSize(32, 32);  // Adjust size as needed
  iconLabel->setVisible(false);  // Start with icon hidden

  messageLabel = new QLabel("Tap mode for more info", this);

  layout->addWidget(iconLabel);
  layout->addWidget(messageLabel);

  setLayout(layout);

  setStyleSheet(R"(
    InfoDisplayBar {
      border: 2px solid #CCCCCC;
      border-radius: 5px;
      background-color: #444444;
    }
    QLabel {
      color: white;
    }
  )");
}

void InfoDisplayBar::setMessage(const QString &message, const QString &iconPath) {
  messageLabel->setText(message);
  if (!iconPath.isEmpty()) {
    iconLabel->setPixmap(QPixmap(iconPath).scaled(32, 32, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    iconLabel->setVisible(true);
  } else {
    iconLabel->setVisible(false);
  }
}
