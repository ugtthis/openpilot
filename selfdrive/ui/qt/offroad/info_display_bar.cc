#include "selfdrive/ui/qt/offroad/info_display_bar.h"

InfoDisplayBar::InfoDisplayBar(QWidget *parent) : QLabel(parent) {
  setAlignment(Qt::AlignLeft | Qt::AlignVCenter);
  setWordWrap(true);
  setStyleSheet(R"(
    font-size: 40px;
    color: white;
    background-color: #000000;
    border: 4px solid #F5F5F5;
    border-radius: 15px;
    padding: 30px 10px 30px 20px;
  )");

  resetTimer = new QTimer(this);
  resetTimer->setSingleShot(true);
  connect(resetTimer, &QTimer::timeout, this, &InfoDisplayBar::resetToDefaultMessage);

  resetToDefaultMessage();
}

void InfoDisplayBar::showModeMessage(DrivingMode mode) {
  QString message;
  switch (mode) {
    case DrivingMode::StockADAS:
      message = "Stock ADAS mode enabled";
      break;
    case DrivingMode::Chill:
      message = "Chill mode enabled";
      break;
    case DrivingMode::Experimental:
      message = "Experimental mode enabled";
      break;
  }
  setText(message);
  resetTimer->start(3000);  // Resets to default message
}

void InfoDisplayBar::resetToDefaultMessage() {
  setText(DEFAULT_MESSAGE);
}
