#include "selfdrive/ui/qt/offroad/info_display_bar.h"
#include <QPixmap>
#include <QPainter>

InfoDisplayBar::InfoDisplayBar(QWidget* parent) : QWidget(parent) {
    setFixedHeight(60);

    layout = new QHBoxLayout(this);
    layout->setContentsMargins(20, 0, 20, 0);
    layout->setSpacing(10);

    iconLabel = new QLabel(this);
    iconLabel->setFixedSize(32, 32);
    iconLabel->setVisible(false);

    messageLabel = new QLabel("Tap mode for more info", this);
    messageLabel->setStyleSheet("font-size: 32px; color: white; font-weight: medium;");

    layout->addWidget(iconLabel);
    layout->addWidget(messageLabel, 1);

    setLayout(layout);
    setStyleSheet("background-color: transparent;");
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