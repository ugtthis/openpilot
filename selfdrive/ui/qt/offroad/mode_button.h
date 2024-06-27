#pragma once

#include <QWidget>
#include <QPushButton>
#include <QLabel>
#include <QTimer>

class ModeButton : public QWidget {
  Q_OBJECT

public:
  explicit ModeButton(QWidget* parent = nullptr);
  void setModeText(const QString &text);
  void setDescriptionText(const QString &text);
  void setGradient(const QColor &startColor, const QColor &endColor);
  void setToggleEnabled(bool enabled);

signals:
  void modeToggled(const QString &mode, bool enabled);
  void descriptionToggled(bool expanded);

private slots:
  void onButtonClicked();
  void onToggleClicked();
  void autoCollapse();

private:
  QLabel *headerText;
  QLabel *descriptionText;
  QPushButton *toggleButton;
  QTimer *collapseTimer;
  bool isExpanded;
  bool isToggledOn;
  QString modeText;
  QString description;
  QColor gradientStart;
  QColor gradientEnd;

protected:
  void paintEvent(QPaintEvent *event) override;
  void mousePressEvent(QMouseEvent *event) override;
};
