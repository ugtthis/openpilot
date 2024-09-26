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

  const double disabledButtonOpacity = 0.3;
  QString textColor = isEnabled ? "white" : "#555555";

  struct GradientColors {
    QColor start;
    QColor end;
  };

  const std::map<DrivingMode, GradientColors> gradients = {
    {DrivingMode::Chill, {QColor("#00c88c"), QColor("#0077be")}},
    {DrivingMode::Experimental, {QColor("#ff8c2f"), QColor("#c62a1d")}},
    {DrivingMode::StockADAS, {QColor("#1f1c18"), QColor("#6e7e8b")}}
  };

  auto colors = gradients.at(mode);

  if (!isEnabled) {
    colors.start.setAlphaF(disabledButtonOpacity);
    colors.end.setAlphaF(disabledButtonOpacity);
  }

  QString backgroundColor = QString("qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, "
                                    "stop:0 %1, stop:1 %2)")
                                    .arg(colors.start.name(QColor::HexArgb),
                                         colors.end.name(QColor::HexArgb));

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
